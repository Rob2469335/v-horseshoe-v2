import yaml

with open('litellm-config.yaml') as f:
    config = yaml.safe_load(f)

print('Model list:')
for m in config.get('model_list', []):
    print(f'  {m["model_name"]}')

print('\nFallbacks:')
for f in config.get('fallbacks', []):
    print(f'  {f}')