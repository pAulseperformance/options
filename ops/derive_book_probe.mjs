// Public orderbook depth probe on Derive. No auth, no account, no capital.
// Channels are public per docs; we read real bids/asks or find there are none.
const WS = "wss://api.derive.xyz/v3/ws";

const TARGETS = [
  "ETH-PERP",                  // control: a live perp must have a book
  "BTC-PERP",                  // control 2
  "ETH-20270924-3000-P",       // 363d put  (the insurance leg)
  "ETH-20270924-3000-C",       // 363d call
  "ETH-20270625-3000-P",       // 272d put
  "ETH-20270326-3000-P",       // 181d put
  "ETH-20261225-2800-P",       // 90d put
  "ETH-20261002-2800-C",       // 6d call (near-dated comparison)
];

const channels = TARGETS.map((t) => `orderbook.${t}.1.10`);
const seen = new Map(); // instrument -> snapshot

const ws = new WebSocket(WS);
let done = false;

const finish = (note) => {
  if (done) return;
  done = true;
  console.log("\n================ RESULTS ================");
  console.log(note ? `(${note})` : "");
  for (const t of TARGETS) {
    const s = seen.get(t);
    if (!s) {
      console.log(`  ${t.padEnd(26)} NO SNAPSHOT RECEIVED`);
      continue;
    }
    const bids = s.bids || [];
    const asks = s.asks || [];
    const bestBid = bids.length ? bids[0][0] ?? bids[0].price ?? JSON.stringify(bids[0]) : "-";
    const bestAsk = asks.length ? asks[0][0] ?? asks[0].price ?? JSON.stringify(asks[0]) : "-";
    console.log(
      `  ${t.padEnd(26)} bids=${String(bids.length).padStart(3)} asks=${String(asks.length).padStart(3)}` +
      `  bestBid=${String(bestBid).padStart(9)} bestAsk=${String(bestAsk).padStart(9)}`
    );
  }
  console.log("=========================================");
  try { ws.close(); } catch {}
  process.exit(0);
};

ws.onopen = () => {
  console.log(`connected -> ${WS}`);
  ws.send(JSON.stringify({ id: 1, method: "subscribe", params: { channels } }));
};

ws.onmessage = (ev) => {
  let msg;
  try { msg = JSON.parse(ev.data); } catch { return; }
  if (msg.id === 1 && msg.result) {
    console.log("subscribe status:", JSON.stringify(msg.result.status));
    console.log("subscribed channels:", (msg.result.current_subscriptions || []).length);
    return;
  }
  if (msg.method === "subscription") {
    const ch = msg.params && msg.params.channel;
    const data = (msg.params && msg.params.data) || {};
    if (!ch || !ch.startsWith("orderbook.")) return;
    const inst = ch.split(".")[1];
    if (!seen.has(inst)) {
      seen.set(inst, data);
      console.log(`  snapshot <- ${inst}  (${(data.bids || []).length} bids / ${(data.asks || []).length} asks)`);
    }
    if (seen.size >= TARGETS.length) finish("all instruments reported");
  }
};

ws.onerror = (e) => console.log("WS ERROR:", e.message || e.type);
ws.onclose = (e) => { if (!done) finish(`socket closed (code ${e.code})`); };

setTimeout(() => finish(`12s elapsed; got ${seen.size}/${TARGETS.length} instruments`), 12000);
