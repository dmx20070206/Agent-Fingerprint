python orchestrator.py \
  --agent browseruse \
  --model deepseek-chat \
  --sandbox-directory sandbox/static \
  --task-file tasks/wheel_scroll.jsonl \
  --run-id browseruse_deepseek_wheel_scroll \
  --no-network-probe \
  --timeout 900