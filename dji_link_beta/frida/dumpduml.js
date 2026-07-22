/*
 * dumpduml.js — Frida hook for DJI Fly (WM160) to capture DUML frames without any
 * drone/RC hardware. Run against DJI Fly in a ROOTED emulator (BlueStacks/AVD) or phone.
 *
 * It hooks the un-obfuscated DUML base class uav.midware.data.manager.P3.DataBase
 * (every Data* model inherits it), so ONE set of hooks catches ALL commands:
 *   - getSendData()      -> every OUTGOING frame  (what the app actually sends)
 *   - setRecData([B)     -> every INCOMING reply/telemetry frame
 *   - setPushRecData([B) -> every INCOMING push frame
 *   - postMockPush(...)  -> the app's own fake-push path (useful for injection later)
 *
 * Each line is tagged with the concrete model class name (e.g. DataCameraRequestSendFiles),
 * which tells us the command semantically even though the wire bytes are what we want.
 *
 * Usage (in the emulator, with frida-server running as root):
 *   frida -U -f dji.go.v5  -l dumpduml.js          (replace with the real package id)
 *   # or attach:  frida -U -n "DJI Fly" -l dumpduml.js
 * Then tap around in the app (open Album, change Max Altitude, etc.) and copy the log.
 *
 * Nothing is modified — this is log-only. Injection helpers are defined but not called.
 */

'use strict';

function hex(bytes) {
    if (bytes === null) return '(null)';
    try {
        var b = Java.array('byte', bytes);
        var s = '';
        for (var i = 0; i < b.length; i++) {
            var v = b[i] & 0xff;
            s += (v < 16 ? '0' : '') + v.toString(16);
        }
        return s;
    } catch (e) { return '(hex err ' + e + ')'; }
}

// Parse a standard DUML frame (55 magic) header so the log is human-readable.
// Layout: [0]=0x55 [1..2]=len/ver [3]=crc8 [4]=sender [5]=receiver [6..7]=seq
//         [8]=cmd_type [9]=cmd_set [10]=cmd_id [11..]=payload [..]=crc16
function duml(h) {
    if (h.length < 22 || h.substr(0, 2) !== '55') return '';
    function btoi(i) { return parseInt(h.substr(i * 2, 2), 16); }
    var sender = btoi(4), recv = btoi(5), ctype = btoi(8), cset = btoi(9), cid = btoi(10);
    return ' [snd=0x' + sender.toString(16) + ' rcv=0x' + recv.toString(16) +
           ' type=0x' + ctype.toString(16) + ' SET=0x' + cset.toString(16) +
           ' ID=0x' + cid.toString(16) + ']';
}

Java.perform(function () {
    var DB = Java.use('uav.midware.data.manager.P3.DataBase');

    function cls(self) {
        try { return self.getClass().getName().replace('uav.midware.data.model.P3.', ''); }
        catch (e) { return '?'; }
    }

    // ---- OUTGOING: getSendData() returns the assembled frame bytes ----
    try {
        DB.getSendData.implementation = function () {
            var out = this.getSendData();
            var h = hex(out);
            console.log('[TX] ' + cls(this) + ' len=' + (out ? out.length : 0) +
                        duml(h) + '  ' + h);
            return out;
        };
    } catch (e) { console.log('!! getSendData hook failed: ' + e); }

    // ---- INCOMING: setRecData([B) = reply/telemetry payload for this model ----
    try {
        DB.setRecData.overload('[B').implementation = function (data) {
            console.log('[RX] ' + cls(this) + ' len=' + (data ? data.length : 0) +
                        '  ' + hex(data));
            return this.setRecData(data);
        };
    } catch (e) { console.log('!! setRecData hook failed: ' + e); }

    // ---- INCOMING push: setPushRecData([B) ----
    try {
        DB.setPushRecData.overload('[B').implementation = function (data) {
            console.log('[PUSH] ' + cls(this) + ' len=' + (data ? data.length : 0) +
                        '  ' + hex(data));
            return this.setPushRecData(data);
        };
    } catch (e) { console.log('!! setPushRecData hook failed: ' + e); }

    console.log('=== dumpduml.js armed: TX/RX/PUSH hooks on DataBase. Tap the app now. ===');
});

/*
 * INJECTION (for the "pretend data arrived from the drone" workflow — enable later):
 * postMockPush([B off len) feeds a raw frame into a model as if the drone sent it.
 * To fake e.g. an OSD-common push, get the model instance and call:
 *   var m = Java.use('uav.midware.data.model.P3.DataOsdGetPushCommon').getInstance();
 *   m.postMockPush(frameBytes, 0, frameBytes.length);
 * We'll wire concrete injections once the TX log tells us the exact frames to replay.
 */
