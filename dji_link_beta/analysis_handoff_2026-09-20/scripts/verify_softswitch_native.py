#!/usr/bin/env python3
"""Офлайн-запуск сохранённого ARM64-конструктора SoftSwitchMode через Unicorn.

Это ограниченный harness сериализатора, а не полноценный runtime Android/DJI Fly и
не проверка самолёта. RTTI, поиск singleton SDK и хранилище Buffer заменены заглушками.
Настоящие setter и конструктор запроса выполняются до SendSetPack, после чего запуск
останавливается. USB, сеть, команды самолёту и изменение прошивки не используются.

Использование: python verify_softswitch_native.py [path/to/libsdk_jni.so]
Зависимость: unicorn (нужен только этому исследовательскому harness).
"""
import hashlib
from pathlib import Path
import struct
import sys

from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm64_const import (UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2,
                                UC_ARM64_REG_X3, UC_ARM64_REG_LR, UC_ARM64_REG_PC,
                                UC_ARM64_REG_SP)


SHA256 = '017d65e3daacf290405339fde62d2ae49bbd5a4c6c3a2c29ee2338a5177a34c0'
SETTER = 0x27a986c
CONSTRUCTOR = 0x27aa72c
SEND_SET_PACK = 0x4a2fc20


def run(blob, mode):
    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    uc.mem_map(0, 0x6000000)
    uc.mem_write(0, blob)
    # Нужный LOAD совпадает с файловым смещением; кроме заглушенного RTTI внешние
    # релокированные данные не разыменуются. Объекты и стек лежат в отдельной памяти.
    objects, stack = 0x10000000, 0x11000000
    uc.mem_map(objects, 0x10000)
    uc.mem_map(stack, 0x10000)
    shared, value, callback = objects + 0x200, objects + 0x300, objects + 0x400
    uc.mem_write(shared, struct.pack('<QQ', value, 0))
    uc.mem_write(value + 8, struct.pack('<I', mode))
    for reg, val in [(UC_ARM64_REG_X0, objects), (UC_ARM64_REG_X1, objects + 0x100),
                     (UC_ARM64_REG_X2, shared), (UC_ARM64_REG_X3, callback),
                     (UC_ARM64_REG_SP, stack + 0xf000)]:
        uc.reg_write(reg, val)
    buffers, result = {}, {}

    def ret():
        uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_LR))

    def hook(uc, address, size, data):
        if address == 0x4a0eb90:  # __dynamic_cast: передан объект нужного типа
            ret()
        elif address == 0x4a2fc10:  # PLT конструктора запроса -> настоящий конструктор
            uc.reg_write(UC_ARM64_REG_PC, CONSTRUCTOR)
        elif address in (0x4a10c30, 0x4a10c40):  # поиск экземпляра отправителя SDK
            uc.reg_write(UC_ARM64_REG_X0, 0)
            ret()
        elif address == 0x4a0ebb0:  # конструктор Buffer с пустым хранилищем
            buffers[uc.reg_read(UC_ARM64_REG_X0)] = b''
            ret()
        elif address == 0x4a0eb00:  # Buffer assign(src, length)
            dst = uc.reg_read(UC_ARM64_REG_X0)
            src = uc.reg_read(UC_ARM64_REG_X1)
            length = uc.reg_read(UC_ARM64_REG_X2)
            buffers[dst] = bytes(uc.mem_read(src, length)) if length else b''
            ret()
        elif address == SEND_SET_PACK:  # здесь пакет передали бы в SDK
            req = uc.reg_read(UC_ARM64_REG_X1)
            header = bytes(uc.mem_read(req, 0x20))
            result.update(cmd_set=header[1], cmd_id=header[2], sender=header[3],
                          receiver=header[4], attr=header[7], payload=buffers[req + 0x20].hex())
            uc.emu_stop()
        elif 0x4a00000 <= address < 0x4b00000:
            raise RuntimeError(f'unexpected external call at {address:#x}')

    uc.hook_add(UC_HOOK_CODE, hook)
    uc.emu_start(SETTER, 0, count=10000)
    if not result:
        raise RuntimeError('конструктор не дошёл до SendSetPack в пределах лимита инструкций')
    expected = dict(cmd_set=6, cmd_id=0x59, sender=2, receiver=3, attr=0x0e,
                    payload=bytes([mode]).hex())
    if result != expected:
        raise AssertionError((result, expected))
    return result


def main():
    root = Path(__file__).resolve().parents[2]
    default = root / '.git' / 'lfs' / 'objects' / SHA256[:2] / SHA256[2:4] / SHA256
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else default
    blob = path.read_bytes()
    if hashlib.sha256(blob).hexdigest() != SHA256:
        raise SystemExit('Другая SDK-библиотека: фиксированные смещения для неё недействительны')
    for mode, name in enumerate(('sport', 'normal', 'cine')):
        print(name, run(blob, mode))
    print('PASS: оригинальный ARM64-конструктор выдал 0x06/0x59 в FLYC для всех трёх режимов')


if __name__ == '__main__':
    main()
