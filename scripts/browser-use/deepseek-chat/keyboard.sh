python orchestrator.py \
  --agent browseruse \
  --model deepseek-chat \
  --sandbox-directory sandbox/static \
  --task-file tasks/keyboard_type.jsonl \
  --run-id browseruse_deepseek_keyboard \
  --no-network-probe \
  --timeout 900