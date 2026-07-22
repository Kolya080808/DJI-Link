/*
 * dumpduml_lite.js — minimal, low-overhead DUML capture for DJI Fly (WM160).
 * The full hook (dumpduml.js) logged every TX/RX/PUSH and overwhelmed ART under
 * BlueStacks' ARM translation (script-load timeout + emulator freeze). This version:
 *   - hooks ONLY getSendData() (outgoing frames — what we actually need first),
 *   - drops the two highest-frequency spam frames (video/OSD) so the log stays small,
 *   - does the minimum work per call.
 * Turn on RX later once TX is captured.
 */
'use strict';

function hex(b) {
    if (b === null) return '';
    var a = Java.array('byte', b), s = '';
    for (var i = 0; i < a.length; i++) { var v = a[i] & 0xff; s += (v < 16 ? '0' : '') + v.toString(16); }
    return s;
}

Java.perform(function () {
    var DB = Java.use('uav.midware.data.manager.P3.DataBase');
    DB.getSendData.implementation = function () {
        var out = this.getSendData();
        try {
            if (out && out.length >= 11) {
                var h = hex(out);
                // bytes: [9]=cmd_set [10]=cmd_id
                var cset = parseInt(h.substr(18, 2), 16);
                var cid = parseInt(h.substr(20, 2), 16);
                // Skip the two spammy frames: video/liveview + OSD-common telemetry.
                var spam = (cset === 0x08) || (cset === 0x03 && cid === 0x43) ||
                           (cset === 0x00 && cid === 0x01);
                if (!spam) {
                    var name = this.getClass().getName().replace('uav.midware.data.model.P3.', '');
                    console.log('[TX] SET=0x' + cset.toString(16) + ' ID=0x' + cid.toString(16) +
                                ' ' + name + '  ' + h);
                }
            }
        } catch (e) {}
        return out;
    };
    console.log('=== lite armed: TX only, spam filtered. Tap Album / Max-Alt now. ===');
});
