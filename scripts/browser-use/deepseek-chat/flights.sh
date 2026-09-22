python orchestrator.py \
  --agent browseruse \
  --model deepseek-chat \
  --sandbox-directory sandbox/flights \
  --task-file tasks/flights.jsonl \
  --run-id browseruse_deepseek_flights \
  --no-network-probe \
  --max-steps 100 \
  --timeout 900