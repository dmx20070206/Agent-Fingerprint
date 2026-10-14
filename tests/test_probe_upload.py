"""Exercise the probe's ACK queue when event payloads exceed keepalive quota."""

import shutil
import subprocess

import pytest


def test_large_event_batch_uploads_and_advances_ack():
    if not shutil.which("node"):
        pytest.skip("node is required for probe execution")
    subprocess.run(
        [
            "node",
            "-e",
            r"""
const assert = require('assert');
global.window = global;
global.location = {href: 'http://example.test/'};
global.addEventListener = global.removeEventListener = () => {};
global.setInterval = global.setTimeout = () => 1;
global.clearInterval = global.clearTimeout = () => {};
global.document = {
  documentElement: {lang: 'en'},
  addEventListener: () => {}, removeEventListener: () => {},
  createElement: () => ({getContext: () => null})
};
global.__AGENT_FINGERPRINT_CONFIG__ = {endpoint: '/events', run_id: 'quota-test'};
const received = [];
let largestBody = 0;
global.fetch = async (url, options) => {
  if (options.method === 'GET') {
    return {ok: true, json: async () => ({ack_seq: received.length})};
  }
  const bytes = Buffer.byteLength(options.body);
  largestBody = Math.max(largestBody, bytes);
  if (options.keepalive && bytes > 65536) throw new TypeError('Failed to fetch');
  const batch = JSON.parse(options.body);
  received.push(...batch.events);
  return {ok: true, json: async () => ({ack_seq: received.length})};
};
console.log = () => {};
require('./src/agent_fingerprint/collection/probes/inject_monitor.js');
(async () => {
  // Enriched targets/composed paths can make a 50-event batch exceed 64 KiB.
  for (let i = 0; i < 60; i++) logEvent('input', {target_context: 'x'.repeat(1600)});
  const monitor = __agentFingerprintMonitor;
  await monitor.flush();
  await monitor.flush();
  assert(largestBody > 65536, 'test must exercise the browser quota');
  assert.strictEqual(monitor.export().last_upload_error, null);
  assert.strictEqual(monitor.export().pending_event_count, 0);
  assert.deepStrictEqual(received, monitor.getEvents());
})().catch(error => { console.error(error); process.exitCode = 1; });
""",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
