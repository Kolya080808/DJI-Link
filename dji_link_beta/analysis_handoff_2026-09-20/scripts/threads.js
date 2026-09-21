function loc(p){const r=Process.findRangeByAddress(p);return {p:String(p),file:r&&r.file?r.file.path:null};}
Interceptor.attach(Module.getGlobalExportByName('pthread_create'),{
 onEnter(args){
  const x={thread:loc(args[2]),arg:String(args[3])};
  try{x.words=[];for(let i=0;i<8;i++)x.words.push(loc(args[3].add(i*8).readPointer()));}catch(e){}
  send(x);
 }
});
for(const name of ['kill','tgkill','raise','abort','exit']){
 const p=Module.findGlobalExportByName(name);if(p)Interceptor.attach(p,{onEnter(a){send({call:name,a0:String(a[0]),a1:String(a[1])});}});
}
send({watching:true});
