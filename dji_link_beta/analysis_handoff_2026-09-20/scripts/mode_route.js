setTimeout(function(){Java.performNow(function(){
  const app=Java.use('android.app.ActivityThread').currentApplication();
  send({application:String(app)});
  Java.classFactory.loader=app.getClassLoader();
  const sdk=Java.use('uav.sdk.UAVSDKManager').d();
  send({sdk_initialized:sdk.h(),products:String(sdk.g())});
  const CB=Java.use('uav.jni.callback.key.JNISetCallback');
  send({set_callback_methods:CB.class.getDeclaredMethods().toString()});
  const Product=Java.use('uav.sdk.keyvalue.value.product.ProductType');
  const Rx=Java.use('com.uav.rx.csdk.RxCSDK');
  const q=Rx.Q.overload('uav.sdk.keyvalue.key.UAVKeyInfo','java.lang.Object');
  q.implementation=function(key, fallback){
    const text=String(key);
    if(text.indexOf('ProductType')!==-1){send({mock:'ProductType only',key:text,value:'UAV59'});return Product.valueOf('UAV59');}
    return q.call(this,key,fallback);
  };
  const set=Rx.u1.overload('uav.sdk.keyvalue.key.UAVKeyInfo','java.lang.Object');
  set.implementation=function(key,value){send({rx_set:String(key),value:String(value)});return set.call(this,key,value);};
  const JNI=Java.use('uav.jni.JNIKeyValue');
  const nativeSet=JNI.native_set;
  nativeSet.implementation=function(a,b,c,d,e,name,bytes,cb){
    send({native_set:String(name),tuple:[a,b,c,d,e],payload:Array.from(bytes).map(x=>(x&255).toString(16).padStart(2,'0')).join('')});
    return nativeSet.call(this,a,b,c,d,e,name,bytes,cb);
  };
  const setter=Java.use('com.uav.flymodel.handwrite.flight.flightosd.v1.V1RCModeChannelKt$v1RCModeChannelValue$2');
  const Channel=Java.use('com.uav.flymodel.generated.api.flight.RCModeChannelValue');
  const Single=Java.use('io.reactivex.Single');
  send({channel_values:String(Channel.values())});
  for(const name of ['CHANNEL_S','CHANNEL_P','CHANNEL_T']){
    try {const value=Channel.valueOf(name);send({enum:name,value:String(value)});const result=setter.$new().b(value);send({requested:name,result:String(result)});result.X0();}
    catch(e){send({requested:name,error:String(e)});}
  }
});}, 2000);
