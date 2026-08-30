"""
QLoRA XPU Training - Qwen3.5-4B Codebase Planner / Reasoner (V4)

Trains on the V4 dataset (real_25_dataset_v4.jsonl): Oracle-context code,
  budget-forced CoT traces, and structured DIAGNOSIS/PLAN/VALIDATION answers.

Architecture (validated 2026-08-29 on 16GB Arc A770):
  - Base: Qwen3.5-4B-Base-HF in 4-bit NF4 + double quant, compute FP16
    (without torch_dtype=torch.float16 the Windows XPU driver crashes with
    SPV_INTEL_bfloat16_arithmetic at load).
  - LoRA: r=8, alpha=16, dropout=0.05 on [q_proj, v_proj, k_proj, o_proj]
  - Gradient checkpointing ON (prepared via prepare_model_for_kbit_training).
  - DYNAMIC per-batch padding: dataset tokenizes with truncation but NO fixed
    padding; DataCollatorForLanguageModeling pads to the longest sample in each
    batch. batch=1 so each step pays only its own real length. A fixed
    padding="max_length" pre-tokenization forces every step to the worst-case
    length and OOMs. Max real row ~2466 tokens; measured ceiling ~2528.

Usage:
  python train_v4.py --smoke     # 2-step validation pass (no save)
  python train_v4.py             # full 5-epoch training -> OUTPUT_DIR/adapter
"""
import os, gc, json, subprocess, sys, torch
from torch.utils.data import Dataset
from transformers import (
    AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig,
    TrainingArguments, DataCollatorForLanguageModeling,
)
from peft import LoraConfig, TaskType, get_peft_model, prepare_model_for_kbit_training
from transformers import Trainer

def _init_msvc():
    vcvars = r"C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
    if os.path.exists(vcvars):
        try:
            output = subprocess.check_output(f'cmd /c "{vcvars}" && set', shell=True).decode('utf-8')
            for line in output.splitlines():
                if '=' in line:
                    key, val = line.split('=', 1)
                    os.environ[key] = val
        except Exception:
            pass
_init_msvc()
try:
    import intel_extension_for_pytorch as ipex  # noqa: F401  (optional on XPU)
except Exception:
    pass

MODEL_PATH = r"C:\Users\rober\models\Qwen3.5-4B-Base-HF"
OUTPUT_DIR = r"C:\Users\rober\Projects\qwen3_5_4b_real25_v4_lora"
DATA_FILE = r"C:\Users\rober\Projects\qwen_train_data\real_25_dataset_v4.jsonl"
MAX_LEN = 2528  # measured ceiling; dataset rows peak ~2466 -> headroom

SMOKE = "--smoke" in sys.argv


def load_data(filepath):
    texts = []
    if not os.path.exists(filepath):
        return texts
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                item = json.loads(line)
                text = item["text"] + "\n<|endoftext|>"
                texts.append(text)
    return texts


class DynTextDataset(Dataset):
    """Dynamic-padding dataset: truncate to MAX_LEN but do NOT pre-pad.
    DataCollatorForLanguageModeling pads per-batch to the longest sample in that
    batch. With batch=1, each step's memory == that sample's real length."""
    def __init__(self, texts, tokenizer):
        self.items = []
        for t in texts:
            enc = tokenizer(t, truncation=True, max_length=MAX_LEN, return_tensors=None, padding=False)
            self.items.append({
                "input_ids": torch.tensor(enc["input_ids"]),
                "attention_mask": torch.tensor(enc["attention_mask"]),
            })
    def __getitem__(self, idx):
        item = {k: v.clone() for k, v in self.items[idx].items()}
        item["labels"] = item["input_ids"].clone()
        return item
    def __len__(self):
        return len(self.items)


def main():
    print(f"Loading tokenizer from {MODEL_PATH}...")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    print(f"Loading data from {DATA_FILE}...")
    texts = load_data(DATA_FILE)
    if not texts:
        print("No training data found!")
        return
    print(f"Loaded {len(texts)} training examples.")

    # sanity: report real lengths so a regression in the dataset never silently
    # truncates away the answer (the tail we must keep). Fail loudly on rows
    # that exceed MAX_LEN rather than quietly chopping.
    for i, t in enumerate(texts):
        n = len(tokenizer.encode(t, add_special_tokens=False))
        if n > MAX_LEN:
            print(f"WARNING: row {i} is {n} tokens > MAX_LEN {MAX_LEN} (will truncate)")

    dataset = DynTextDataset(texts, tokenizer)

    print("Configuring 4-bit quantization for XPU...")
    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True,
        bnb_4bit_compute_dtype=torch.float16,
    )

    print("Loading base model...")
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_PATH,
        quantization_config=bnb_config,
        device_map="xpu",
        torch_dtype=torch.float16,
        low_cpu_mem_usage=True,
    )

    model.config.use_cache = False
    model = prepare_model_for_kbit_training(model)

    peft_config = LoraConfig(
        task_type=TaskType.CAUSAL_LM,
        r=8, lora_alpha=16, lora_dropout=0.05,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
    )
    model = get_peft_model(model, peft_config)
    model.print_trainable_parameters()

    if SMOKE:
        training_args = TrainingArguments(
            output_dir=os.path.join(OUTPUT_DIR, "smoke"),
            per_device_train_batch_size=1,
            gradient_accumulation_steps=1,
            max_steps=2,
            optim="adamw_torch",
            fp16=True,
            report_to="none",
            logging_steps=1,
            gradient_checkpointing=True,
            remove_unused_columns=False,
            save_strategy="no",
        )
        print("SMOKE MODE: 2 steps, no save.")
    else:
        training_args = TrainingArguments(
            output_dir=OUTPUT_DIR,
            per_device_train_batch_size=1,
            # accum=1: this is the exact footprint the smoke test validated.
            # v3 inherited gradient_accumulation_steps=4, which OOM'd the combined
            # run (peak `9.84 GiB allocated`) — the accumulation window holds 4
            # micro-batches of backward work + the ~2.4GB fp32 logits before the
            # optimizer step. batch=1 on only 23 rows means accum>1 buys nothing.
            gradient_accumulation_steps=1,
            learning_rate=2e-4,
            num_train_epochs=5,
            save_strategy="epoch",
            warmup_steps=5,
            optim="adamw_torch",
            fp16=True,
            logging_steps=2,
            report_to="none",
            gradient_checkpointing=True,
            remove_unused_columns=False,
        )

    collator = DataCollatorForLanguageModeling(tokenizer=tokenizer, mlm=False)
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=dataset,
        data_collator=collator,
    )

    print("Starting XPU training...")
    trainer.train()

    if SMOKE:
        print("SMOKE PASS: 2 steps trained (no adapter saved).")
        return

    print(f"Saving final adapter to {OUTPUT_DIR}/adapter...")
    trainer.model.save_pretrained(os.path.join(OUTPUT_DIR, "adapter"))
    tokenizer.save_pretrained(os.path.join(OUTPUT_DIR, "adapter"))
    print("Done!")

    del trainer, model
    gc.collect()
    torch.xpu.empty_cache()


if __name__ == "__main__":
    main()