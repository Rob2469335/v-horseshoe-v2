import importlib
import traceback

class SelfHeal:
    def __init__(self):
        self.failed_modules = {}

    def safe_import(self, module: str):
        try:
            return importlib.import_module(module)
        except Exception as e:
            self.failed_modules[module] = str(e)
            return None

    def heal_imports(self, modules):
        recovered = {}
        for m in modules:
            mod = self.safe_import(m)
            if mod:
                recovered[m] = "ok"
            else:
                recovered[m] = "quarantined"
        return recovered
