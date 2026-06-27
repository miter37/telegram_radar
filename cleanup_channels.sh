#!/usr/bin/env bash
# Cleanup tool: disable channels in channels.json that aren't broadcast
# channels (e.g. accidentally added personal @username that resolved to
# PeerUser). These IDs never produce live messages because NewMessage
# broadcasts only come from TLChannel (Channel/Supergroup).

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Load TG_* from .env or shell
[ -f .env ] && { set -a; . ./.env; set +a; }

# Run inline python that uses the worker's validate method via direct
# Telethon client (simpler than spinning up the full GUI).
exec python3 -c "
import asyncio, json
from pathlib import Path
from telethon import TelegramClient
from telethon.tl.types import Channel as TLChannel

CFG_PATH = Path('data/channels.json')
data = json.loads(CFG_PATH.read_text(encoding='utf-8')) if CFG_PATH.exists() else []

async def main():
    client = TelegramClient(
        str(Path('data/market_radar.session')),
        int('${TG_API_ID}'),
        '${TG_API_HASH}',
    )
    await client.connect()
    if not await client.is_user_authorized():
        print('Session not authorized. Run the app once to log in.')
        return
    good, bad = [], []
    for entry in data:
        cid = int(entry.get('id', 0))
        if not cid: continue
        try:
            entity = await client.get_entity(cid)
        except Exception as e:
            bad.append((entry, f'get_entity: {e.__class__.__name__}'))
            continue
        if isinstance(entity, TLChannel):
            good.append(entry)
            print(f'  OK    @{entry[\"username\"]}  (id={cid}, title={entity.title!r})')
        else:
            kind = type(entity).__name__
            bad.append((entry, f'{kind} — broadcast 채널이 아님'))
            print(f'  BAD   @{entry[\"username\"]}  (id={cid}) → {kind}  (broadcast 채널이 아님)')
    await client.disconnect()
    if bad:
        keep = [e for e, _ in bad if not e.get('enabled', True)]
        kept_data = [e for e in data if any(e.get('id') == cid for cid, _ in [])] if False else (
            [e for e in data if e not in [b for b, _ in bad]]
        )
        CFG_PATH.write_text(json.dumps(kept_data, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'')
        print(f'Removed {len(bad)} non-broadcast channels from channels.json.')
        print(f'Remaining: {len(kept_data)} channels.')
        print(f'')
        print(f'Add a real broadcast channel:')
        print(f'  python run.py  →  ⚙ 채널 관리  →  @kiwoom_us_toktok (public 채널이어야 함)')
    else:
        print('All enabled channels are valid broadcast channels.')

asyncio.run(main())
"