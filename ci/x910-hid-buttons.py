"""X910: NativeHidInput at the base pin learns report 2's Power and Volume bits, as upstream's ShengHidInput 71a40e7 has them.

The X910 has the same PMIC wiring as sheng (TWRP devicetree: pmic_pwrkey, pmic_resin, gpio-keys volume_up on GPIO 6, active low);
sheng's ButtonProbe.cpp reads them and calls the sink set here. Without the boot argument gate: this kernel is the only one that has it.
"""
import sys
from pathlib import Path

hid, probe = Path(sys.argv[1]), Path(sys.argv[2])
text = hid.read_text()


def replace(old, new):
    global text
    if text.count(old) != 1:
        raise SystemExit('Pinned NativeHidInput context changed: ' + old[:50])
    text = text.replace(old, new)


replace('''    0x85,0x02,0x05,0x0c,0x09,0x40,0x15,0x00,0x25,0x01,
    0x75,0x01,0x95,0x01,0x81,0x02,
    0x75,0x07,0x95,0x01,0x81,0x03,0xc0''', '''    0x85,0x02,0x05,0x0c,0x09,0x40,0x09,0x30,0x09,0xe9,0x09,0xea,0x15,0x00,0x25,0x01,
    0x75,0x01,0x95,0x04,0x81,0x02,
    0x75,0x04,0x95,0x01,0x81,0x03,0xc0''')
replace('static const unsigned char descriptor[]={',
        'extern "C" void native_buttons_set_sink(void (*)(void *, uint32_t), void *) __attribute__((weak_import));\nstatic const unsigned char descriptor[]={')
replace('    bool hasEventDriverCached() {', '''    uint8_t buttons=0;   // report 2 bits held by the hardware buttons
    static void buttonsNotify(void *context,uint32_t mask) {
        NativeHidInput *self=static_cast<NativeHidInput *>(context);
        if(self && self->inputLoop)self->inputLoop->runAction(&NativeHidInput::buttonsAction,self,reinterpret_cast<void *>(uintptr_t(mask)));
    }
    static IOReturn buttonsAction(OSObject *owner,void *mask,void *,void *,void *) {
        NativeHidInput *self=OSDynamicCast(NativeHidInput,owner);if(!self)return kIOReturnSuccess;
        self->buttons=static_cast<uint8_t>(uintptr_t(mask)&0xe);
        if(!self->hasEventDriverCached())return kIOReturnSuccess;
        unsigned char report[]={2,self->buttons};
        IOBufferMemoryDescriptor *buffer=IOBufferMemoryDescriptor::withBytes(report,sizeof(report),kIODirectionNone);
        IOReturn result=buffer?self->handleReport(buffer,0,0):kIOReturnNoMemory;
        OSSafeReleaseNULL(buffer);
        native_hid_input_observed(4,result);
        return kIOReturnSuccess;
    }
    bool hasEventDriverCached() {''')
replace('        unsigned char report[]={2,static_cast<unsigned char>(self->down?0:1)};',
        '        unsigned char report[]={2,static_cast<unsigned char>((self->down?0:1)|self->buttons)};')
replace('        setProperty("OfflineInputProbe",true);\n',
        '        if(!touchOnly && &native_buttons_set_sink)native_buttons_set_sink(&NativeHidInput::buttonsNotify,this);\n        setProperty("OfflineInputProbe",true);\n')
replace('    void stop(IOService *provider) override {\n',
        '    void stop(IOService *provider) override {\n        if(!touchOnly && &native_buttons_set_sink)native_buttons_set_sink(nullptr,nullptr);\n')
hid.write_text(text)

text = probe.read_text()
replace('    if (!PE_parse_boot_argn("sheng-buttons", &enabled, sizeof enabled) || enabled != 1 || native_buttons_stats[0]) return;\n',
        '    (void) enabled;\n    if (native_buttons_stats[0]) return;\n')
probe.write_text(text)
print('X910 buttons added to', hid)
