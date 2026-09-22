python orchestrator.py \
  --agent browseruse \
  --model deepseek-chat \
  --sandbox-directory sandbox/static \
  --task-file tasks/mouse_click.jsonl \
  --run-id browseruse_deepseek_mouse_click \
  --no-network-probe \
  --timeout 900