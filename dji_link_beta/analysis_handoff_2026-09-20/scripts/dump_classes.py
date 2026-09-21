from pathlib import Path
import sys
sys.path.insert(0,str(Path('scratch/research_deps').resolve()))
from loguru import logger
logger.remove()
from androguard.core.dex import DEX
p=Path(sys.argv[1])
d=DEX(p.read_bytes())
with Path(sys.argv[2]).open('w',encoding='utf-8') as out:
 for c in d.get_classes():
  if not any(s in c.get_name() for s in sys.argv[3:]): continue
  out.write('\nCLASS '+c.get_name()+'\n')
  for f in c.get_fields(): out.write('FIELD '+f.get_name()+' '+f.get_descriptor()+'\n')
  for m in c.get_methods():
   out.write('\nMETHOD '+m.get_name()+m.get_descriptor()+'\n')
   if m.get_code():
    for i in m.get_instructions():out.write(i.get_name()+' '+i.get_output()+'\n')
