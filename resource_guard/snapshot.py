"""Bounded, credential-free resource history for future incident diagnosis."""
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from datetime import datetime

root = Path(__file__).resolve().parents[1]
handler = RotatingFileHandler(root / 'data/logs/resources.jsonl', maxBytes=5*1024**2, backupCount=3)
logger = logging.getLogger('resources')
logger.setLevel(logging.INFO)
logger.addHandler(handler)
memory = {}
for line in Path('/proc/meminfo').read_text().splitlines():
    key, rest = line.split(':', 1)
    if key in ('MemTotal','MemAvailable','SwapTotal','SwapFree'):
        memory[key] = int(rest.split()[0]) * 1024
processes = []
for path in Path('/proc').iterdir():
    if not path.name.isdigit(): continue
    try:
        status = dict(line.split(':',1) for line in (path/'status').read_text().splitlines() if ':' in line)
        rss = int(status.get('VmRSS','0 kB').split()[0]) * 1024
        processes.append({'pid':int(path.name),'name':status['Name'].strip(),'rss':rss})
    except (OSError, ValueError, KeyError): continue
groups = {}
for name in ('yiyin-bot.service','yiyin-napcat.service'):
    directory = Path('/sys/fs/cgroup/yiyin.slice') / name
    try:
        groups[name] = {key: (directory/key).read_text().strip() for key in ('memory.current','memory.peak','memory.swap.current','memory.events')}
    except OSError: pass
logger.info(json.dumps({'time':datetime.now().astimezone().isoformat(),'memory':memory,
    'pressure':Path('/proc/pressure/memory').read_text().strip(),'groups':groups,
    'top':sorted(processes,key=lambda p:p['rss'],reverse=True)[:12]},ensure_ascii=False))
