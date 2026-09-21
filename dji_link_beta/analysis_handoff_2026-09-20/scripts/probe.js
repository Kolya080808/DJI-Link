send({arch:Process.arch, modules:Process.enumerateModules().filter(m=>/sdk|nativebridge|ndk_translation/.test(m.name)).map(m=>({name:m.name,base:m.base.toString()}))});
Java.perform(function(){
  send({java:Java.available});
  for(const name of ['uav.jni.JNIKeyValue','uav.jni.JNIUsbAccessory','com.uav.flymodel.handwrite.flight.flightosd.v1.V1RCModeChannelKt$v1RCModeChannelValue$2','uav.sdk.keyvalue.value.product.ProductType']) {
    try {const c=Java.use(name);send({class:name,methods:c.class.getDeclaredMethods().toString(),fields:c.class.getDeclaredFields().toString()});}
    catch(e){send({class:name,error:String(e)});}
  }
});
