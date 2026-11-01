#!/usr/bin/env python3
"""Pantau beast: kirim notifikasi mint BSC baru ke Telegram.

Sumber: beastfarmer.xyz/monitorbnb dan dataflux.my.id.
Tiap kontrak baru dicek on-chain (read-only) lalu dikirim sekali ke Telegram.
Tidak pernah menandatangani atau mengirim transaksi.
"""
import json, os, sys, time, html, urllib.request

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT = os.environ["TELEGRAM_CHAT_ID"]
RPCS = os.environ.get("BSC_RPC", "https://bsc-rpc.publicnode.com,https://bsc-dataseed.bnbchain.org").split(",")
WALLETS = [w.strip() for w in os.environ.get("WATCH_WALLETS", "").split(",") if w.strip()]
STATE = os.environ.get("STATE_FILE", "state/seen.json")
LOOP_SECONDS = int(os.environ.get("LOOP_SECONDS", "0"))
POLL = int(os.environ.get("POLL_SECONDS", "60"))
UA = {"User-Agent": "Mozilla/5.0 pantau-beast"}
SKIP_STATUS = {"NOT_BSC"}

def get(url, data=None, headers=None, timeout=20):
    req = urllib.request.Request(url, data=data, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())

# ---------- feeds ----------
def feed_beast():
    out = []
    try:
        d = get("https://beastfarmer.xyz/monitorbnb/api/raw?bsc=1&limit=100")
        for it in d.get("items", []):
            ca = (it.get("ca") or "").lower()
            if not ca.startswith("0x") or it.get("status") in SKIP_STATUS:
                continue
            out.append({"ca": ca, "name": it.get("project") or it.get("post_name") or "?",
                        "type": it.get("type") or "", "status": it.get("status") or "",
                        "price": it.get("price_bnb"), "seen": it.get("seen_count") or 1,
                        "src": "beastfarmer"})
    except Exception as e:
        print("beastfarmer error:", e, file=sys.stderr)
    return out

def feed_dataflux():
    out = []
    try:
        d = get(f"https://dataflux.my.id/api/status?t={int(time.time())}")
        for it in d.get("feed", []):
            ca = (it.get("contract") or "").lower()
            if not ca.startswith("0x"):
                continue
            tags = [t.get("text", "") for t in it.get("tags", [])]
            if any("GAGAL" in t for t in tags):
                continue
            desc = (it.get("description") or "").split("\n")[0][:120]
            out.append({"ca": ca, "name": desc or it.get("title") or "?", "type": "",
                        "status": " ".join(tags), "price": None, "seen": 1, "src": "dataflux"})
    except Exception as e:
        print("dataflux error:", e, file=sys.stderr)
    return out

# ---------- chain (read-only) ----------
def rpc(method, params):
    for url in RPCS:
        try:
            body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
            r = get(url.strip(), body, {"Content-Type": "application/json"}, 15)
            if "result" in r:
                return r["result"]
        except Exception:
            continue
    return None

def call(to, data, frm=None):
    tx = {"to": to, "data": data}
    if frm:
        tx["from"] = frm
    return rpc("eth_call", [tx, "latest"])

def dec_str(h):
    try:
        b = bytes.fromhex(h[2:])
        if len(b) >= 64:
            n = int.from_bytes(b[32:64], "big")
            return b[64:64 + n].decode(errors="ignore").strip("\x00")
        return b.rstrip(b"\x00").decode(errors="ignore")
    except Exception:
        return ""

def dec_int(h):
    try:
        return int(h, 16) if h and h != "0x" else None
    except Exception:
        return None

PRICE_SIGS = {  # common price views
    "0x6817c76c": "mintPrice()", "0xa035b1fe": "price()", "0x13faede6": "cost()",
    "0x8d859f3e": "PRICE()", "0xc002d23d": "MINT_PRICE()", "0xf7d97577": "publicPrice()",
}

def enrich(c):
    ca = c["ca"]
    code = rpc("eth_getCode", [ca, "latest"])
    if not code or code == "0x":
        c["dead"] = True
        return c
    c["proxy"] = "363d3d373d3d3d363d73" in code or len(code) < 400
    c["delegatecall"] = "f4" in code[2:] and False  # opcode scan is noisy; left off
    nm = call(ca, "0x06fdde03"); sy = call(ca, "0x95d89b41")
    if nm: c["onchain_name"] = dec_str(nm)
    if sy: c["symbol"] = dec_str(sy)
    is721 = call(ca, "0x01ffc9a780ac58cd" + "0" * 56)
    is1155 = call(ca, "0x01ffc9a7d9b67a26" + "0" * 56)
    if dec_int(is721) == 1: c["std"] = "ERC-721"
    elif dec_int(is1155) == 1: c["std"] = "ERC-1155"
    elif call(ca, "0x313ce567"): c["std"] = "ERC-20"
    c["supply"] = dec_int(call(ca, "0x18160ddd"))
    for sel in ("0xd5abeb01", "0x32cb6b0c", "0x2fb098d2"):  # maxSupply, MAX_SUPPLY, maxTokens-ish
        v = dec_int(call(ca, sel))
        if v:
            c["max"] = v; break
    for sel in PRICE_SIGS:
        v = dec_int(call(ca, sel))
        if v is not None and v < 10**20:
            c["price_wei"] = v; break
    if WALLETS and c.get("std") in ("ERC-721", "ERC-20"):
        held = []
        for w in WALLETS:
            v = dec_int(call(ca, "0x70a08231" + "0" * 24 + w.lower()[2:]))
            held.append(bool(v))
        c["held"] = held
    return c

# ---------- message ----------
def fmt(c):
    e = html.escape
    name = c.get("onchain_name") or c["name"]
    lines = [f"🚨 <b>{e(name)}</b>" + (f" ({e(c['symbol'])})" if c.get("symbol") else "")]
    if c.get("price_wei") is not None:
        p = c["price_wei"] / 1e18
        lines.append("💰 Harga: " + ("<b>GRATIS</b> + gas" if p == 0 else f"{p:.6g} BNB + gas"))
    elif c.get("price") not in (None, ""):
        lines.append(f"💰 Harga (feed): {e(str(c['price']))} BNB")
    else:
        lines.append("💰 Harga: belum terbaca")
    t = c.get("std") or c.get("type") or "?"
    lines.append(f"🧩 Tipe: {e(t)} · BSC")
    if c.get("supply") is not None and c.get("max"):
        pct = c["supply"] * 100 // max(c["max"], 1)
        bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
        lines.append(f"📈 {bar} {c['supply']}/{c['max']} ({pct}%)")
        if c["supply"] >= c["max"]:
            lines.append("⛔ SOLD OUT")
    elif c.get("supply") is not None and c.get("std") != "ERC-20":
        lines.append(f"📈 Minted: {c['supply']}")
    lines.append(f"📣 Call: <b>{c.get('seen', 1)}x</b> di channel" + (" 🔥" if c.get('seen', 1) >= 4 else ""))
    if c.get("status"):
        lines.append(f"🏷 Status: {e(c['status'])}")
    if c.get("held"):
        lines.append("👛 Wallet: " + " ".join(f"W{i+1} {'✅' if h else '—'}" for i, h in enumerate(c["held"])))
    if c.get("proxy"):
        lines.append("⚠️ Kontrak kecil/proxy, cek dulu")
    lines.append(f"📄 <code>{c['ca']}</code>")
    lines.append(f'🔗 <a href="https://bscscan.com/address/{c["ca"]}">BscScan</a> · sumber: {c["src"]}')
    return "\n".join(lines)

def send(text):
    body = json.dumps({"chat_id": CHAT, "text": text, "parse_mode": "HTML",
                       "disable_web_page_preview": True}).encode()
    get(f"https://api.telegram.org/bot{TOKEN}/sendMessage", body, {"Content-Type": "application/json"})

# ---------- main ----------
def load():
    try:
        return json.load(open(STATE))
    except Exception:
        return {}

def save(s):
    os.makedirs(os.path.dirname(STATE) or ".", exist_ok=True)
    # keep last 2000
    items = sorted(s.items(), key=lambda kv: kv[1])[-2000:]
    json.dump(dict(items), open(STATE, "w"))

def once(seen, first_run):
    found = {}
    for c in feed_beast() + feed_dataflux():
        if c["ca"] in found:
            found[c["ca"]]["seen"] = found[c["ca"]]["seen"] + c["seen"]
            found[c["ca"]]["src"] = "beastfarmer + dataflux"
            continue
        found[c["ca"]] = c
    new = [c for ca, c in found.items() if ca not in seen]
    for c in new:
        seen[c["ca"]] = int(time.time())
        if first_run:
            continue  # baseline: jangan spam item lama
        try:
            c = enrich(c)
            if c.get("dead"):
                continue
            send(fmt(c))
            time.sleep(1)
        except Exception as e:
            print("send error:", c["ca"], e, file=sys.stderr)
    return len(new)

def main():
    seen = load()
    first = not seen
    end = time.time() + LOOP_SECONDS
    while True:
        n = once(seen, first)
        save(seen)
        print(f"{time.strftime('%H:%M:%S')} baru: {n}{' (baseline)' if first else ''}")
        if first:
            send(f"✅ Pantau beast aktif. Baseline {len(seen)} kontrak dicatat, berikutnya hanya yang baru.")
        first = False
        if time.time() + POLL > end:
            break
        time.sleep(POLL)

if __name__ == "__main__":
    main()
