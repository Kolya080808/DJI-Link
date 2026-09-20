import json
from pathlib import Path
import sys
import time
import threading
import frida
import frida_tools

root = Path(__file__).resolve().parent
sys.stdout = (root / (Path(sys.argv[2]).stem+'.runtime.log')).open('w', encoding='utf-8', buffering=1)
device = frida.get_device('emulator-5554', timeout=10)
print('DEVICE_READY', flush=True)
spawn = sys.argv[1] == 'spawn'
pid = device.spawn(['dji.go.v5']) if spawn else int(sys.argv[1])
print('PID', pid, flush=True)
source = Path(sys.argv[2]).read_text(encoding='utf-8')
realm = sys.argv[3] if len(sys.argv)>3 else 'native'
session = device.attach(pid, realm=realm)
print('ATTACHED', flush=True)
bridge = (Path(frida_tools.__file__).parent/'bridges/java.js').read_text(encoding='utf-8')
bootstrap = '''
for (const name of ['exit','_exit','abort']) {
  const p=Module.findGlobalExportByName(name);
  if(p) Interceptor.attach(p,{onEnter(a){send({exit_call:name,code:a[0].toInt32(),caller:String(this.returnAddress),backtrace:Thread.backtrace(this.context,Backtracer.ACCURATE).map(DebugSymbol.fromAddress).map(String)});}});
}
const guardNoop = new NativeCallback(function(arg){ return ptr(0); }, 'pointer', ['pointer']);
Interceptor.attach(Module.getGlobalExportByName('pthread_create'), {
  onEnter(args) {
    const range=Process.findRangeByAddress(args[2]);
    if(range && range.file && range.file.path.endsWith('/libAppGuard-x86.so')) {
      const off=args[2].sub(range.base).add(range.file.offset);
      send({instrumentation:'observe AppGuard worker',entry:String(args[2]),offset:String(off),file:range.file.path});
      Interceptor.attach(args[2],{onEnter(){send({guard_thread:Process.getCurrentThreadId(),offset:String(off)});}});
    }
  }
});
Process.setExceptionHandler(function(e) {
  send({exception:e.type,address:String(e.address),thread:Process.getCurrentThreadId(),pc:String(e.context.pc)});
  if (Process.arch==='x64' && e.context.rip.isNull()) {
    send({nullCallReturn:String(e.context.rsp.readPointer())});
  }
  return false;
});
'''
java_exit_probe = '''
Java.performNow(function(){
  const Runtime=Java.use('java.lang.Runtime');
  const exit=Runtime.exit.overload('int');
  exit.implementation=function(code){send({java_exit:code,stack:Java.use('android.util.Log').getStackTraceString(Java.use('java.lang.Exception').$new())});return exit.call(this,code);};
});
'''
script = session.create_script(bootstrap+bridge+'\nglobalThis.Java=bridge;\n'+java_exit_probe+source)
script.on('message', lambda m, data: print(json.dumps(m, ensure_ascii=False), flush=True))
script.load()
if spawn:
    device.resume(pid)
def probe_arm():
    try:
        arm_session = device.attach(pid, realm='emulated')
        arm_script = arm_session.create_script("send({arm_arch:Process.arch,modules:Process.enumerateModules().filter(m=>m.name.indexOf('sdk')!==-1).map(m=>({name:m.name,base:String(m.base)}))});")
        arm_script.on('message', lambda m,data: print(json.dumps(m), flush=True))
        arm_script.load()
        time.sleep(5)
        arm_session.detach()
    except Exception as e:
        print('ARM_PROBE '+repr(e), flush=True)
threading.Timer(4, probe_arm).start()
time.sleep(45)
script.unload()
session.detach()
