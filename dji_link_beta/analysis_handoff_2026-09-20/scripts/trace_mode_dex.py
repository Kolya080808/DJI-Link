from pathlib import Path
import sys
sys.path.insert(0, str(Path('scratch/research_deps').resolve()))
from loguru import logger
logger.remove()
from androguard.core.dex import DEX

out = Path('scratch/android_runtime/mode_callers.txt')
with out.open('w', encoding='utf-8') as f:
    for p in Path('dji_link_beta/reverse_docs/unpacked_app_dex').glob('*.dex'):
        data = p.read_bytes()
        if not any(s in data for s in (b'SoftSwitchMode', b'FlightModeSwitch', b'JNIKeyValue')):
            continue
        print(p.name, flush=True)
        d = DEX(data)
        for c in d.get_classes():
            for m in c.get_methods():
                if not m.get_code():
                    continue
                ins = list(m.get_instructions())
                lines = [i.get_name() + ' ' + i.get_output() for i in ins]
                if (any(s in c.get_name() for s in ('V1RCModeChannelKt','JNIKeyValue','JNIUsbAccessory','SoftSwitch')) or any('SoftSwitchMode' in s for s in lines)):
                    f.write('\nCLASS '+c.get_name()+' METHOD '+m.get_name()+m.get_descriptor()+'\n')
                    f.write('\n'.join(lines)+'\n')
print(out)
