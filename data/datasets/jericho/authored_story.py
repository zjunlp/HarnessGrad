"""SPDX-License-Identifier: CC0-1.0

Original CC0 Z-machine v5 game for native Jericho controls, not a benchmark ROM.

Implements the public TextWorld debug/status protocol supported by unmodified
Jericho 3.3.1 libfrotz. No story/compiler/library bytes are copied from a game.
"""
import struct


def story_bytes():
    image = bytearray(4096)
    def word(address, value):
        struct.pack_into('>H', image, address, value)
    image[0] = 5
    for address,value in ((4,0x500),(6,0x500),(8,0x480),(10,0x220),
                          (12,0x40),(14,0x500),(24,0x4a0),(26,1024)):
        word(address,value)
    image[18:24] = b'261107'
    image[0x380] = 100
    image[0x420] = 10
    image[0x480:0x484] = bytes((0,6,0,0))
    word(0x40,1)

    def packed(text):
        chars=[]
        for c in text:
            if c==' ': chars.append(0)
            elif 'a'<=c<='z': chars.append(ord(c)-91)
            else:
                value=13 if c=='\n' else ord(c)
                chars.extend((5,6,value>>5,value&31))
        while len(chars)%3: chars.append(5)
        result=bytearray()
        for i in range(0,len(chars),3):
            value=(chars[i]<<10)|(chars[i+1]<<5)|chars[i+2]
            result.extend(struct.pack('>H',value|(0x8000 if i+3==len(chars) else 0)))
        return bytes(result)

    # Real v5 object tree: room -> player, token. Taking inserts token under player.
    for n,name,parent,sibling,child in ((1,'room',0,0,2),(2,'player',1,3,0),(3,'token',1,0,0)):
        entry=0x220+126+(n-1)*14
        word(entry+6,parent)
        word(entry+8,sibling)
        word(entry+10,child)
        prop=0x300+(n-1)*32
        word(entry+12,prop)
        text=packed(name)
        image[prop]=len(text)//2
        image[prop+1:prop+1+len(text)]=text
        image[prop+1+len(text)]=0

    code=bytearray();labels={};fixups=[]
    def emit(*values): code.extend(values)
    def label(name): labels[name]=len(code)
    def text(value): code.extend(b'\xb2'+packed(value))
    def jump(name):
        emit(0x8c);fixups.append((len(code),name,False));emit(0,0)
    def equal(variable,value,target):
        emit(0xc1,0x9f,variable,value)
        fixups.append((len(code),target,True));emit(0,0)
    def char(index): emit(0xd0,0x1f,3,0x80,index,0x13)
    def status():
        text(' -');emit(0xe6,0xbf,0x11);text('/');emit(0xe6,0xbf,0x12);text('\n>')
    label('start');emit(0xf2,0x7f,0);text('\nauthored token room');status()
    label('read');emit(0xe2,0x17,3,0x80,1,0);emit(0xe4,0x0f,3,0x80,4,0x20,0x13)
    char(2);equal(0x13,ord('t'),'t-command');equal(0x13,ord('f'),'finish')
    jump('look')
    label('t-command');char(3);equal(0x13,ord('w'),'debug')
    # take token: update actual object tree and native status protocol.
    emit(0x0e,3,2,0x0d,0x11,1);jump('look')
    label('debug');char(11);equal(0x13,ord('m'),'maximum');equal(0x13,ord('p'),'player')
    text('3\n>');jump('read')
    label('maximum');text('2\n>');jump('read')
    label('player');text('2\n>');jump('read')
    label('look');emit(0x95,0x12);text('\nauthored token room');status();jump('read')
    label('finish');equal(0x11,1,'win');jump('look')
    label('win');emit(0x95,0x12,0x0d,0x11,2);text('\n*** The End ***');status();jump('read')
    for position,target,branch in fixups:
        offset=labels[target]-(position+2)+2
        if branch:
            encoded=(offset&0x3fff)|0x8000
        else: encoded=offset&0xffff
        struct.pack_into('>H',code,position,encoded)
    image[0x500:0x500+len(code)]=code
    word(28,sum(image[64:])&0xffff)
    return bytes(image)
