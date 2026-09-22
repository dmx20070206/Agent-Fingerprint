python orchestrator.py \
  --agent browseruse \
  --model deepseek-chat \
  --sandbox-directory sandbox/forums \
  --task-file tasks/forums.jsonl \
  --run-id browseruse_deepseek_forums \
  --no-network-probe \
  --timeout 900