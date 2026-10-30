// X910 adaptation of pinned sheng LegacyDisplayProbe.cpp. BSD-2-Clause-Patent.
// SPDX-License-Identifier: BSD-2-Clause-Patent
// Bounded offline original lifecycle instrument; no desktop claim.
#include <IOKit/IOPlatformExpert.h>
#include <IOKit/IODeviceMemory.h>
#include <IOKit/IODeviceTreeSupport.h>
#include <IOKit/IOLib.h>
#include <IOKit/IOUserClient.h>
#include <libkern/c++/OSMetaClass.h>
#include <libkern/c++/OSSerialize.h>
#include <libkern/c++/OSData.h>
#include <libkern/c++/OSSymbol.h>
#include <kern/task.h>
#include <sys/proc.h>
#include <pexpert/pexpert.h>
#include <mach/kmod.h>
#include <machine/machine_routines.h>
extern "C" kmod_info_t g_kernel_kmod_info;
extern "C" void native_legacy_display_static_init(void) __asm__("__GLOBAL__sub_I_NativeLegacyDisplay.cpp");
extern "C" IOService *native_legacy_display_allocate(void);
extern "C" void native_gop_presenter_static_init(void) __asm__("__GLOBAL__sub_I_NativeGOPPresenter.cpp");
extern "C" IOService *native_gop_presenter_create(void);
#include <kern/thread_call.h>
#include <kern/clock.h>
// sheng's SM8550 EPSS/L3 frequency votes (kits/sheng/CpuFreqProbe.cpp, sheng-cpufreq=<n>) and ArmCpu's SMP evidence.
extern "C" void sheng_cpufreq_probe(void);
extern "C" void sheng_cpufreq_poll(void);
extern "C" char native_cpufreq_row[120];
extern "C" volatile uint32_t native_cpufreq_stage;
extern "C" volatile uint32_t armcpu_smp_requested,armcpu_smp_running,armcpu_smp_conduit,armcpu_smp_step[8];
extern "C" volatile int32_t armcpu_smp_psci_rc[8];
extern "C" void native_keyevents_static_init(void) __asm__("__GLOBAL__sub_I_NativeKeyStoreEvents.cpp");
extern "C" IOService *native_keyevents_allocate(void);
static IOService *pendingLegacyDisplay;
static IOService *activeGOPPresenter,*activeGOPNub;
// GPU hand-off presenter (sheng-fb-gpu=1, phase 4): lives beside the display driver, which offers it frames instead of copying them.
static IOService *gpuHandoffPresenter;
extern "C" bool native_gop_gpu_offer(IOService *,void *display,IOMemoryDescriptor *,uint64_t stride,uint32_t width,uint32_t height);
extern "C" void native_gop_presenter_set_source(IOService *presenter,IOService *nub);
volatile uint32_t native_legacy_gpu_fallbacks,native_legacy_gpu_last_status;
static IOService *activeLegacyDisplay;
// Keep one diagnostic mapping alive until reboot, even after backend stop.
// The pulse is permanently closed before any system-frame copy.
static IORegistryEntry *unitScaleChosen;
static bool unitScaleAdded,unitRotationAdded;
static void finishUnitScalePolicy(bool keep) {
    if(unitScaleChosen && !keep) {
        if(unitScaleAdded)unitScaleChosen->removeProperty("display-scale");
        if(unitRotationAdded)unitScaleChosen->removeProperty("display-rotation");
    }
    OSSafeReleaseNULL(unitScaleChosen);unitScaleAdded=false;unitRotationAdded=false;
}
static bool publishOrMatchWord(IORegistryEntry *entry,const char *key,uint32_t value,bool &added) {
    OSObject *previous=entry->getProperty(key);
    if(previous) {
        OSData *data=OSDynamicCast(OSData,previous);uint32_t word=0;
        if(!data || data->getLength()!=sizeof(word))return false;
        memcpy(&word,data->getBytesNoCopy(),sizeof(word));return word==value;
    }
    OSData *data=OSData::withBytes(&value,sizeof(value));
    bool result=data && entry->setProperty(key,data);OSSafeReleaseNULL(data);added=result;return result;
}
extern "C" {
volatile uint32_t native_legacy_stage;
volatile int native_legacy_meta_status=-1;
volatile uint32_t native_legacy_init_result,native_legacy_start_result,native_legacy_probe_match;
volatile uint32_t native_legacy_parent_match,native_legacy_object_size,native_legacy_released;
volatile uint64_t native_legacy_object,native_legacy_nub,native_legacy_workloop;
volatile uint32_t native_legacy_guard_slot;
volatile uint64_t native_legacy_guard_object,native_legacy_guard_caller;
volatile int native_legacy_surface_status=-1;
volatile uint32_t native_legacy_surface_found;
volatile uint32_t x910_display_deferred,x910_display_data_ready;
volatile uint64_t x910_surface_wait_us;
volatile uint64_t native_legacy_video_base,native_legacy_video_bytes;
volatile uint64_t native_legacy_gop_map,native_legacy_gop_virtual,native_legacy_gop_physical,native_legacy_gop_bytes;
volatile uint32_t native_legacy_gop_width,native_legacy_gop_height;
volatile uint32_t native_legacy_registration_calls,native_legacy_properties_length;
volatile uint32_t native_legacy_dt_attached;
volatile int native_legacy_unit_scale_status=-1;
volatile uint32_t native_legacy_unit_scale_calls,native_legacy_chosen_scale,native_legacy_chosen_rotation;
volatile uint32_t native_legacy_pm_stage,native_legacy_pm_calls,native_legacy_pm_callbacks,native_legacy_pm_ordinal;
volatile int native_legacy_pm_status=-1;
volatile uint32_t native_legacy_swap_validation_calls,native_legacy_swap_validation_result,native_legacy_swap_validation_reason;
volatile uint64_t native_legacy_swap_request,native_legacy_swap_surface;
volatile uint32_t native_legacy_transfer_calls;
volatile int native_legacy_transfer_status=-1;
volatile uint64_t native_legacy_transfer_bytes,native_legacy_transfer_request;
volatile uint32_t native_legacy_retirement_calls,native_legacy_retired_generation,native_legacy_presented_generation,native_legacy_retired_frames;
volatile uint64_t native_legacy_retired_current,native_legacy_retired_pending;
char native_legacy_properties[4096];
volatile uint32_t native_legacy_client_calls,native_legacy_client_type;
volatile int native_legacy_client_pid,native_legacy_client_status=-1;
volatile uint64_t native_legacy_client_task,native_legacy_client_object;
char native_legacy_client_class[64];
extern kern_return_t OSKextLoadKextWithIdentifier(const char *);
volatile uint32_t native_legacy_prepare_calls,native_legacy_prepare_reason,native_legacy_prepare_attempts;
volatile uint32_t native_legacy_prepare_failures,native_legacy_prepare_retried,native_legacy_prepare_max_attempts;
volatile uint64_t native_legacy_prepare_wait_us;
static uint64_t probeNowUs(void) {uint64_t ns;absolutetime_to_nanoseconds(mach_absolute_time(),&ns);return ns/1000;}
bool native_legacy_prepare_scanout(void) {
    native_legacy_prepare_calls=native_legacy_prepare_calls+1;native_legacy_prepare_reason=0;
    if (ml_at_interrupt_context()) {native_legacy_prepare_reason=1;return false;}
    if (!ml_get_interrupts_enabled()) {native_legacy_prepare_reason=2;return false;}
    // X910 has no asynchronous BootProgress painter to coordinate with.
    return true;
}
__attribute__((noinline)) void native_legacy_display_checkpoint(void) {__asm__ volatile("" ::: "memory");}
__attribute__((noinline)) void native_legacy_gop_mapping_checkpoint(void) {__asm__ volatile("" ::: "memory");}
__attribute__((noinline)) void native_legacy_client_checkpoint(void) {__asm__ volatile("" ::: "memory");}
void native_legacy_pm_observed(unsigned stage,IOReturn status,unsigned long ordinal) {
    native_legacy_pm_stage=stage;native_legacy_pm_status=status;native_legacy_pm_ordinal=(uint32_t)ordinal;
    native_legacy_pm_calls=native_legacy_pm_calls+1;
    if(stage==3)native_legacy_pm_callbacks=native_legacy_pm_callbacks+1;
}
__attribute__((noinline)) void native_legacy_swap_validation_checkpoint(bool,unsigned,const void *,const void *) {__asm__ volatile("" ::: "memory");}
// Last rejected swap request, copied from the original request layout the validator reads (offsets
// qualified in NativeLegacyDisplay::validateSwap). Observation only; nothing here changes the verdict.
volatile uint32_t native_legacy_swap_accepted_count,native_legacy_swap_rejected_count,native_legacy_rej_reason;
volatile uint64_t native_legacy_rej_surfaces[4];
volatile uint32_t native_legacy_rej_state[3],native_legacy_rej_options[3],native_legacy_rej_size[2],native_legacy_rej_rects[2][4];
char native_legacy_rej_class[32];
volatile uint64_t native_legacy_rej_surface[7];   // width, height, format, planes, stride, allocation, descriptor bytes of the last rejected surface
void native_legacy_surface_rejected(uint64_t width,uint64_t height,uint32_t format,uint32_t planes,uint64_t stride,uint64_t allocation,uint64_t bytes) {
    native_legacy_rej_surface[0]=width;native_legacy_rej_surface[1]=height;native_legacy_rej_surface[2]=format;native_legacy_rej_surface[3]=planes;
    native_legacy_rej_surface[4]=stride;native_legacy_rej_surface[5]=allocation;native_legacy_rej_surface[6]=bytes;
}
static void recordRejectedSwap(unsigned reason,const void *request,const void *surface) {
    const unsigned char *bytes=reinterpret_cast<const unsigned char *>(request);
    if(!bytes)return;
    native_legacy_rej_reason=reason;
    uint64_t surfaces[4]={};memcpy(surfaces,bytes+2624,sizeof(surfaces));
    for(unsigned i=0;i<4;++i)native_legacy_rej_surfaces[i]=surfaces[i];
    const unsigned stateOffsets[3]={792,796,800},optionOffsets[3]={804,808,812};
    for(unsigned i=0;i<3;++i){uint32_t a=0,b=0;memcpy(&a,bytes+stateOffsets[i],4);memcpy(&b,bytes+optionOffsets[i],4);
        native_legacy_rej_state[i]=a;native_legacy_rej_options[i]=b;}
    uint32_t w=0,h=0;memcpy(&w,bytes+816,4);memcpy(&h,bytes+820,4);native_legacy_rej_size[0]=w;native_legacy_rej_size[1]=h;
    const unsigned rectOffsets[2]={17,81};
    for(unsigned r=0;r<2;++r){uint32_t rect[4]={};memcpy(rect,bytes+rectOffsets[r],sizeof(rect));
        for(unsigned i=0;i<4;++i)native_legacy_rej_rects[r][i]=rect[i];}
    char name[32]={};
    if(surface){const OSObject *object=reinterpret_cast<const OSObject *>(surface);
        const OSMetaClass *meta=object->getMetaClass();const char *cls=meta?meta->getClassName():nullptr;
        if(cls)strlcpy(name,cls,sizeof(name));}
    memcpy(native_legacy_rej_class,name,sizeof(name));
}
// Content statistics of presented frames (copy = kind 0, blank fill = kind 1): the first 32 are kept, with the time
// in seconds since the first frame. A per-frame record shows the animation cadence and when the OS blanks the panel.
#define NATIVE_LEGACY_FRAMES 32
volatile uint32_t native_legacy_frame_count;
volatile uint32_t native_legacy_frame_kind[NATIVE_LEGACY_FRAMES];
volatile uint32_t native_legacy_frame_time[NATIVE_LEGACY_FRAMES];
volatile uint64_t native_legacy_frame_stats[NATIVE_LEGACY_FRAMES][5];
static uint64_t frameEpoch;
void native_legacy_frame_observed(unsigned kind,uint64_t count,uint64_t minx,uint64_t miny,uint64_t maxx,uint64_t maxy) {
    uint32_t n=native_legacy_frame_count;
    uint64_t now,frequency;
    __asm__ volatile("isb\n\tmrs %0, cntpct_el0":"=r"(now));__asm__ volatile("mrs %0, cntfrq_el0":"=r"(frequency));
    if(!frameEpoch)frameEpoch=now;
    if(n<NATIVE_LEGACY_FRAMES) {
        native_legacy_frame_kind[n]=kind;
        native_legacy_frame_time[n]=frequency?(uint32_t)((now-frameEpoch)/frequency):0;
        native_legacy_frame_stats[n][0]=count;native_legacy_frame_stats[n][1]=minx;native_legacy_frame_stats[n][2]=miny;
        native_legacy_frame_stats[n][3]=maxx;native_legacy_frame_stats[n][4]=maxy;
    }
    native_legacy_frame_count=n+1;
}
volatile uint32_t native_legacy_frame_colors[8][4];
volatile uint64_t native_legacy_frame_color_counts[8][4],native_legacy_frame_color_other[8];
// Called right after the matching native_legacy_frame_observed(); fills slot count-1 (or the blank slot).
void native_legacy_frame_colors_observed(const uint32_t *colors,const uint64_t *counts,uint64_t other) {
    uint32_t n=native_legacy_frame_count;
    if(!n || n>8)return;   // dominant colours are kept for the first eight frames only
    n-=1;
    for(unsigned i=0;i<4;++i){native_legacy_frame_colors[n][i]=colors[i];native_legacy_frame_color_counts[n][i]=counts[i];}
    native_legacy_frame_color_other[n]=other;
}
// The life of every accepted swap, from the kernel's side: validated (the client submitted it), offered to the GPU copy (or started on the CPU),
// and retired. Times are microseconds since boot; the last 64 are kept.
struct SwapEvent {uint64_t submitted,offered,done;uint32_t number;};
static SwapEvent swapEvents[64];static uint32_t swapEventCount;
extern "C" void native_legacy_swap_stage(unsigned stage) {
    const uint64_t now=probeNowUs();
    uint32_t total=swapEventCount;
    if(stage==0) {
        SwapEvent &e=swapEvents[total%64];e={now,0,0,total+1};swapEventCount=total+1;return;
    }
    uint32_t first=total>64?total-64:0;
    for(uint32_t i=first;i<total;++i) {
        SwapEvent &e=swapEvents[i%64];
        if(stage==1 && !e.offered && !e.done){e.offered=now;return;}
        if(stage==2 && !e.done){e.done=now;if(!e.offered)e.offered=now;return;}
    }
}

// The display refresh tick (NativeLegacyDisplay, sheng-vsync): vsync messages sent, arms, idle stops, late ticks, starts, refresh
// properties reported at that rate, and the rate.
static volatile uint64_t vsyncEvents[6];
static volatile unsigned vsyncHz;
void native_legacy_vsync_observed(unsigned event,unsigned hz) {
    if(event<6)vsyncEvents[event]=vsyncEvents[event]+1;
    vsyncHz=hz;
}

void native_legacy_swap_validation_observed(bool accepted,unsigned reason,const void *request,const void *surface) {
    if(accepted)native_legacy_swap_stage(0);
    if(accepted)native_legacy_swap_accepted_count=native_legacy_swap_accepted_count+1;
    else {native_legacy_swap_rejected_count=native_legacy_swap_rejected_count+1;recordRejectedSwap(reason,request,surface);}
    native_legacy_swap_validation_calls=native_legacy_swap_validation_calls+1;
    native_legacy_swap_validation_result=accepted;native_legacy_swap_validation_reason=reason;
    native_legacy_swap_request=(uintptr_t)request;native_legacy_swap_surface=(uintptr_t)surface;
    native_legacy_swap_validation_checkpoint(accepted,reason,request,surface);
}
// Time of the pixel copy into the scanout buffer (counter ticks): swaps completed, last/maximum/sum, for the banner.
volatile uint64_t native_legacy_copy_n,native_legacy_copy_last,native_legacy_copy_max,native_legacy_copy_sum,native_legacy_copy_bytes_last,native_legacy_copy_bytes_sum;
void native_legacy_copy_timing_observed(uint64_t ticks,uint64_t bytes) {
    native_legacy_copy_bytes_last=bytes;native_legacy_copy_bytes_sum=native_legacy_copy_bytes_sum+bytes;
    native_legacy_copy_n=native_legacy_copy_n+1;native_legacy_copy_last=ticks;native_legacy_copy_sum=native_legacy_copy_sum+ticks;
    if(ticks>native_legacy_copy_max)native_legacy_copy_max=ticks;
}
__attribute__((noinline)) void native_legacy_transfer_observed(IOReturn status,const void *request,uint64_t bytes) {
    native_legacy_transfer_calls=native_legacy_transfer_calls+1;
    if(status==kIOReturnSuccess)native_legacy_swap_stage(2);
    native_legacy_transfer_status=status;native_legacy_transfer_request=(uintptr_t)request;native_legacy_transfer_bytes=bytes;

}
bool native_legacy_gpu_offer(void *display,IOMemoryDescriptor *memory,uint64_t stride,uint32_t width,uint32_t height) {
    return gpuHandoffPresenter && native_gop_gpu_offer(gpuHandoffPresenter,display,memory,stride,width,height);
}
void native_legacy_gpu_fallback_observed(int status) {
    native_legacy_gpu_last_status=(uint32_t)status;native_legacy_gpu_fallbacks=native_legacy_gpu_fallbacks+1;
}
__attribute__((noinline)) void native_legacy_retirement_observed(IOService *display) {
    const unsigned char *bytes=reinterpret_cast<const unsigned char *>(display);
    uint64_t current=0,pending=0;uint32_t completed=0,presented=0,frames=0;
    memcpy(&current,bytes+5472,8);memcpy(&pending,bytes+5480,8);
    memcpy(&completed,bytes+5820,4);memcpy(&presented,bytes+5824,4);memcpy(&frames,bytes+252,4);
    native_legacy_retired_current=current;native_legacy_retired_pending=pending;
    native_legacy_retired_generation=completed;native_legacy_presented_generation=presented;native_legacy_retired_frames=frames;
    native_legacy_retirement_calls=native_legacy_retirement_calls+1;
}
// The display object's properties as they are now (the registration copy above predates setDisplayRefreshProperties and the rest): for the
// UFS log. Returns the bytes written (NUL-terminated).

void native_legacy_registration_observed(IOService *display) {
    native_legacy_registration_calls=native_legacy_registration_calls+1;
    OSSerialize *xml=OSSerialize::withCapacity(2048);
    if(xml && display->serializeProperties(xml)) {
        unsigned size=xml->getLength();if(size>=sizeof(native_legacy_properties))size=sizeof(native_legacy_properties)-1;
        memcpy(native_legacy_properties,xml->text(),size);native_legacy_properties[size]=0;native_legacy_properties_length=size;
    }
    OSSafeReleaseNULL(xml);
}
void native_legacy_client_enter(task_t task,uint32_t type) {
    native_legacy_client_calls=native_legacy_client_calls+1;native_legacy_client_task=(uintptr_t)task;
    native_legacy_client_type=type;native_legacy_client_pid=task?proc_pid((proc_t)get_bsdtask_info(task)):0;
}
// The display object's state words the original wait selector tests (offsets 5832..5864, as dwords), for the log.

void native_legacy_client_observed(IOReturn status,IOUserClient *client) {
    native_legacy_client_status=status;native_legacy_client_object=(uintptr_t)client;
    if(client)strlcpy(native_legacy_client_class,client->getMetaClass()->getClassName(),sizeof(native_legacy_client_class));
    native_legacy_client_checkpoint();
}
__attribute__((noinline)) bool native_legacy_gop_mapping_observed(IOService *object,IOMemoryMap *mapping,uint32_t width,uint32_t height) {
    if((uintptr_t)object!=native_legacy_object)panic("NativeLegacyDisplay: wrong mapping owner");
    native_legacy_gop_map=(uintptr_t)mapping;native_legacy_gop_virtual=mapping->getVirtualAddress();
    native_legacy_gop_physical=mapping->getPhysicalAddress();native_legacy_gop_bytes=mapping->getLength();
    native_legacy_gop_width=width;native_legacy_gop_height=height;

    native_legacy_gop_mapping_checkpoint();
    unsigned enabled=0;PE_parse_boot_argn("offline-legacy-unit-scale",&enabled,sizeof(enabled));
    if(!enabled)return true;
    native_legacy_unit_scale_calls=native_legacy_unit_scale_calls+1;native_legacy_unit_scale_status=1;
    unsigned phase=0;PE_parse_boot_argn("offline-legacy-phase",&phase,sizeof(phase));
    PE_Video video={};
    // The value is the UI scale: logical points = actual scanout pixels / scale (1..3). It changes
    // only the published display-scale; the scanout copy still moves one actual pixel per pixel.
    if(enabled<1 || enabled>3 || phase!=4 || unitScaleChosen || PE_current_console(&video) ||
       video.v_width!=width || video.v_height!=height || video.v_rotate ||
       mapping->getPhysicalAddress()!=video.v_baseAddr)return false;
    // Explicit offline UI policy: `enabled` actual GOP scanout pixels per logical
    // point in each direction (1 = native pixels). This is not physical pixel density,
    // panel timing, an Apple device identity or a synthesized mode list.
    unitScaleChosen=IORegistryEntry::fromPath("/chosen",gIODTPlane);
    if(!unitScaleChosen || !publishOrMatchWord(unitScaleChosen,"display-scale",enabled,unitScaleAdded) ||
       !publishOrMatchWord(unitScaleChosen,"display-rotation",video.v_rotate,unitRotationAdded)) {
        finishUnitScalePolicy(false);return false;
    }
    OSData *scale=OSDynamicCast(OSData,unitScaleChosen->getProperty("display-scale"));
    OSData *rotation=OSDynamicCast(OSData,unitScaleChosen->getProperty("display-rotation"));
    uint32_t actualScale=0,actualRotation=0;
    if(!scale || !rotation || scale->getLength()!=4 || rotation->getLength()!=4) {
        finishUnitScalePolicy(false);return false;
    }
    memcpy(&actualScale,scale->getBytesNoCopy(),4);memcpy(&actualRotation,rotation->getBytesNoCopy(),4);
    native_legacy_chosen_scale=actualScale;native_legacy_chosen_rotation=actualRotation;
    native_legacy_unit_scale_status=actualScale==enabled && actualRotation==video.v_rotate?0:2;
    if(native_legacy_unit_scale_status){finishUnitScalePolicy(false);return false;}
    return true;
}
__attribute__((noreturn,noinline)) void native_legacy_backend_guard(unsigned slot,void *object,void *caller) {
    native_legacy_guard_slot=slot;native_legacy_guard_object=(uintptr_t)object;native_legacy_guard_caller=(uintptr_t)caller;
    panic("NativeLegacyDisplay: leaf=%u caller=%llx stage=%u PM=%u swap=%u/%u copy=%08x bytes=%llu retire=%u handoff=%u/%u",
      slot,(uint64_t)(uintptr_t)caller,native_legacy_stage,native_legacy_pm_stage,
      native_legacy_swap_validation_result,native_legacy_swap_validation_reason,
      (unsigned)native_legacy_transfer_status,native_legacy_transfer_bytes,native_legacy_retirement_calls,
      native_legacy_prepare_reason,native_legacy_prepare_attempts);
}

extern void x910_ui_set_display_observer(void (*)(char *,unsigned long));
static void x910_keystore_snapshot(char *out,unsigned long capacity);
static void x910_display_snapshot(char *out,unsigned long capacity) {
    if(!out || !capacity)return;
    unsigned long used=strlen(out);if(used>=capacity)return;
    PE_Video video={};int videoError=PE_current_console(&video);
    snprintf(out+used,capacity-used,
      "\nDISPLAY stage=%u meta=%d init=%u probe=%u start=%u reg=%u parent=%u released=%u"
      "\nIOSurface status=%d found=%u DT=%u scale_status=%d PM=%u/%x deferred=%u afterData=%u wait_ms=%llu"
      "\nVIDEO err=%d base=%llx %llux%llu stride=%llu depth=%llu display=%llu rotate=%llu"
      "\nGOP map=%llx %ux%u scale=%u rotation=%u clients=%u pid=%d status=%x copies=%u bytes=%llu",
      native_legacy_stage,native_legacy_meta_status,native_legacy_init_result,native_legacy_probe_match,
      native_legacy_start_result,native_legacy_registration_calls,native_legacy_parent_match,native_legacy_released,
      native_legacy_surface_status,native_legacy_surface_found,native_legacy_dt_attached,native_legacy_unit_scale_status,
      native_legacy_pm_stage,(unsigned)native_legacy_pm_status,x910_display_deferred,x910_display_data_ready,
      (unsigned long long)(x910_surface_wait_us/1000),videoError,
      (unsigned long long)video.v_baseAddr,(unsigned long long)video.v_width,(unsigned long long)video.v_height,
      (unsigned long long)video.v_rowBytes,(unsigned long long)video.v_depth,(unsigned long long)video.v_display,(unsigned long long)video.v_rotate,
      (unsigned long long)native_legacy_gop_physical,native_legacy_gop_width,native_legacy_gop_height,
      native_legacy_chosen_scale,native_legacy_chosen_rotation,native_legacy_client_calls,native_legacy_client_pid,
      (unsigned)native_legacy_client_status,native_legacy_transfer_calls,(unsigned long long)native_legacy_transfer_bytes);
    // Presentation path, observed live: swap validation verdicts, transfers to the GOP, retirement and the refresh tick.
    used=strlen(out);if(used>=capacity)return;
    snprintf(out+used,capacity-used,
      "\nSWAP calls=%u ok=%u rej=%u last=%u/%u xfer=%u st=%x copy_n=%llu retire=%u gen=%u/%u frames=%u"
      "\nREJ %.31s req=%ux%u surf=%llux%llu fmt=%llx planes=%llu stride=%llu alloc=%llu"
      "\nVSYNC %llu/%llu/%llu/%llu/%llu/%llu hz=%u gpu=%u/%x prep=%u/%u/%u client type=%u %.40s",
      native_legacy_swap_validation_calls,native_legacy_swap_accepted_count,native_legacy_swap_rejected_count,
      native_legacy_swap_validation_result,native_legacy_swap_validation_reason,native_legacy_transfer_calls,
      (unsigned)native_legacy_transfer_status,(unsigned long long)native_legacy_copy_n,native_legacy_retirement_calls,
      native_legacy_retired_generation,native_legacy_presented_generation,native_legacy_frame_count,
      (const char *)native_legacy_rej_class,native_legacy_rej_size[0],native_legacy_rej_size[1],
      (unsigned long long)native_legacy_rej_surface[0],(unsigned long long)native_legacy_rej_surface[1],
      (unsigned long long)native_legacy_rej_surface[2],(unsigned long long)native_legacy_rej_surface[3],
      (unsigned long long)native_legacy_rej_surface[4],(unsigned long long)native_legacy_rej_surface[5],
      (unsigned long long)vsyncEvents[0],(unsigned long long)vsyncEvents[1],(unsigned long long)vsyncEvents[2],
      (unsigned long long)vsyncEvents[3],(unsigned long long)vsyncEvents[4],(unsigned long long)vsyncEvents[5],vsyncHz,
      native_legacy_gpu_fallbacks,native_legacy_gpu_last_status,native_legacy_prepare_calls,native_legacy_prepare_reason,
      native_legacy_prepare_failures,native_legacy_client_type,(const char *)native_legacy_client_class);
    x910_keystore_snapshot(out,capacity);
}

// AppleKeyStore notification and state service (pinned UniXNU NativeKeyStoreEvents, as sheng runs it with sheng-aks=1).
// This port has no SEP, so no AppleKeyStore exists; MobileKeyBag's MKBEventsRegister then returns NULL and SessionCore's
// liveactivitiesd force-unwraps it and traps (hardware C19: SpringBoard -> chronod -> com.apple.sessionservices). With
// x910-aks=1, and only while no AppleKeyStore class is registered, the service is published before launchd. Its user
// client answers the state queries truthfully for this system (no keybag, no passcode, unlocked since boot) and fails
// every key, keybag or data-protection selector: it protects nothing.
volatile uint32_t native_keyevents_stage,native_keyevents_refused_opens,native_aks_opens,native_aks_calls;
volatile int native_keyevents_status=-1;
static volatile uint32_t aksCount[128];
static volatile int32_t aksResult[128];
void native_keyevents_open_refused(void) {native_keyevents_refused_opens=native_keyevents_refused_opens+1;}
bool native_aks_state_enabled(void) {
    unsigned value=0;return PE_parse_boot_argn("x910-aks",&value,sizeof(value)) && value==1;
}
uint32_t native_aks_lock_state(void) {return 0;}
void native_aks_opened(void) {native_aks_opens=native_aks_opens+1;}
void native_aks_call(uint32_t selector,int32_t result) {
    native_aks_calls=native_aks_calls+1;
    if(selector<128){aksCount[selector]=aksCount[selector]+1;aksResult[selector]=result;}
}
static void x910_keystore_probe(void) {
    if(!native_aks_state_enabled() || native_keyevents_stage)return;
    native_keyevents_stage=1;
    const OSSymbol *name=OSSymbol::withCString("AppleKeyStore");
    bool absent=name && !OSMetaClass::getMetaClassWithName(name);
    OSSafeReleaseNULL(name);
    if(!absent){native_keyevents_status=kIOReturnExclusiveAccess;return;}
    IOService *provider=IOService::getResourceService();
    void *handle=provider?OSMetaClass::preModLoad(g_kernel_kmod_info.name):nullptr;
    if(!handle){native_keyevents_status=kIOReturnNoResources;return;}
    native_keyevents_static_init();
    native_keyevents_status=OSMetaClass::postModLoad(handle);
    if(native_keyevents_status)return;
    native_keyevents_stage=2;
    IOService *service=native_keyevents_allocate();
    bool initialized=service && service->init();
    bool attached=initialized && service->attach(provider);
    bool started=attached && service->start(provider);
    if(!started) {
        if(attached)service->detach(provider);
        OSSafeReleaseNULL(service);
        native_keyevents_status=kIOReturnError;return;
    }
    native_keyevents_status=kIOReturnSuccess;native_keyevents_stage=3;
}
// A cluster whose cores are all off reads disabled, so sheng's votes are retried every second for two minutes, which
// covers the secondary cores coming up with sheng-smp=1 (sheng polls from its log rounds for the same reason).
static thread_call_t cpufreqCall;
static unsigned cpufreqTicks;
static void x910_cpufreq_tick(thread_call_param_t,thread_call_param_t) {
    sheng_cpufreq_poll();
    if(++cpufreqTicks<120) {
        uint64_t deadline=0;clock_interval_to_deadline(1,(uint32_t)NSEC_PER_SEC,&deadline);
        thread_call_enter_delayed(cpufreqCall,deadline);
    }
}
static void x910_cpufreq_start(void) {
    sheng_cpufreq_probe();
    if(!native_cpufreq_stage || cpufreqCall)return;
    cpufreqCall=thread_call_allocate(x910_cpufreq_tick,nullptr);
    if(cpufreqCall)x910_cpufreq_tick(nullptr,nullptr);
}
// x910-touch-probe=1, stage 1 of docs/touch-x910-plan.md: read-only state of the Goodix GT9916 path left by the boot
// chain. GCC branch-clock CBCRs (bit 31 CLK_OFF) of the QUPv3 wrapper 1 and its SE4 (I2C4 at 0xa90000), TLMM GPIO 24
// (reset) and 25 (interrupt) CFG/IN_OUT, and only when every one of those clocks is running, the SE4's firmware revision
// (protocol in bits 15:8), FIFO-interface-disable and status words. Offsets: Linux 7.2-rc3 gcc-sm8550.c, geni-se.h,
// sm8550.dtsi (facts of the SoC). Nothing is written. GPIO 36-39 are TrustZone-reserved and never read.
volatile uint32_t x910_touch_stage,x910_touch_clk[5],x910_touch_gpio[4],x910_touch_se[4];
static uint32_t x910_mmio_read(uint64_t physical,uint32_t offset) {
    IODeviceMemory *memory=IODeviceMemory::withRange(physical,0x1000);
    IOMemoryMap *map=memory?memory->map(kIOMapAnywhere|kIOMapInhibitCache):nullptr;
    uint32_t value=0xffffffff;
    if(map)value=*reinterpret_cast<volatile uint32_t *>(map->getVirtualAddress()+offset);
    OSSafeReleaseNULL(map);OSSafeReleaseNULL(memory);
    return value;
}
// x910-touch-probe=2 and 3, stages 2 and 3: C26 showed the boot chain leaves the SE4 serial and slave-AHB clocks off and
// the GT9916 in reset (GPIO 24 and 25 low, inputs with pull-down). Each step is saved to Data (the report file):
//  A. GCC branch vote register 0x152008 and the SE4 RCG (CMD 0x1184f0, CFG 0x1184f4), read only.
//  P. Level 3 only (C27: reset released, the interrupt line never rose in 3 s): power the controller as Linux does, with
//     active-only RPMh votes for LDO12B 1.8 V (VDDIO) and LDO14B 3.2 V (AVDD): voltage, HPM mode (7), enable, at the
//     addresses cmd-db (0x81c60000) gives for "ldob12"/"ldob14", sent through active TCS 0 of the apps RSC DRV2
//     (0x17a20000, TCS block +0xd00, sm8550.dtsi) and polled to completion. Layouts: Linux cmd-db.c, rpmh-rsc.c,
//     qcom-rpmh-regulator.c (facts of the SoC firmware interface).
//  B. Reset: GPIO 24 driven low for 10 ms (level 3), then high (IN_OUT bit 1, then CFG OE, function 0, 2 mA, no pull).
//     GPIO 25 stays an input with pull-down, so it reads 0 unless a powered controller drives its idle-high interrupt
//     line; sampled every 0.5 ms for 3 s (first high in ms, high samples, edges, final level, GPIO 24 input).
//  C. Vote the SE4 and wrapper slave-AHB clocks on (bits 26 and 21 of 0x152008, gcc-sm8550.c). The SE4 clock must clear
//     CLK_OFF; the slave AHB has hardware gating (CBCR bit 1, hwcg in gcc-sm8550.c) and reads CLK_OFF while idle, so at
//     level 3 its vote is what counts (Linux skips that halt check too).
//  D. With the clocks running, the SE4 words of stage 1.
// The SE4 is not configured and no I2C transfer is made.
volatile uint32_t x910_touch_vote,x910_touch_rcg[2],x910_touch_irq[5]={0xffffffff,0,0,0,0};
volatile uint32_t x910_touch_rsc[3],x910_touch_ldo[2],x910_touch_cmd[6];   // rsc: id, TCS 0 status, TCS 0 enable before
extern "C" int x910_report_file_write(const char *text,unsigned long length);
struct X910Mmio { IODeviceMemory *memory; IOMemoryMap *map; volatile uint32_t *base; };
static bool x910_mmio_open(X910Mmio &m,uint64_t physical,uint64_t size=0x1000) {
    m.memory=IODeviceMemory::withRange(physical,size);
    m.map=m.memory?m.memory->map(kIOMapAnywhere|kIOMapInhibitCache):nullptr;
    m.base=m.map?reinterpret_cast<volatile uint32_t *>(m.map->getVirtualAddress()):nullptr;
    return m.base!=nullptr;
}
static void x910_mmio_close(X910Mmio &m) {OSSafeReleaseNULL(m.map);OSSafeReleaseNULL(m.memory);m.base=nullptr;}
static void x910_touch_line2(char *out,unsigned long capacity) {
    snprintf(out,capacity,"TOUCH2 st=%u vote=%x rcg=%x/%x clk=%x/%x irq=%d/%u/%u/%u/%u se=%x/%x/%x/%x\n"
      "RPMH rsc=%x/%x/%x ldo14=%x ldo12=%x cmd=%x/%x/%x/%x/%x/%x",x910_touch_stage,
      x910_touch_vote,x910_touch_rcg[0],x910_touch_rcg[1],x910_touch_clk[0],x910_touch_clk[2],(int)x910_touch_irq[0],
      x910_touch_irq[1],x910_touch_irq[2],x910_touch_irq[3],x910_touch_irq[4],
      x910_touch_se[0],x910_touch_se[1],x910_touch_se[2],x910_touch_se[3],
      x910_touch_rsc[0],x910_touch_rsc[1],x910_touch_rsc[2],x910_touch_ldo[0],x910_touch_ldo[1],
      x910_touch_cmd[0],x910_touch_cmd[1],x910_touch_cmd[2],x910_touch_cmd[3],x910_touch_cmd[4],x910_touch_cmd[5]);
}
static void x910_touch_save(void) {
    static char text[640],line[448];uint64_t ns=0;absolutetime_to_nanoseconds(mach_absolute_time(),&ns);
    x910_touch_line2(line,sizeof(line));
    int n=snprintf(text,sizeof(text),"X910REPORT-BEGIN round=0 uptime=%llu s touch\n%s\nX910REPORT-END\n",
      (unsigned long long)(ns/NSEC_PER_SEC),line);
    if(n>0 && (unsigned)n<sizeof(text))x910_report_file_write(text,(unsigned long)n);
}
// cmd-db lookup: header {u32 version; u8 magic[4]; 8 x rsc_hdr (16 bytes: slv_id, header_offset, data_offset, cnt, ...);
// u32 checksum; u32 reserved} then data; entries of 24 bytes {u8 id[8]; u32 priority[2]; u32 addr; u16 len, offset}.
// Byte reads only (device mapping).
static unsigned x910_db16(volatile uint8_t *bytes,unsigned at) {return (unsigned)bytes[at]|(unsigned)bytes[at+1]<<8;}
static uint32_t x910_cmd_db_addr(volatile uint32_t *db,const char *name) {
    volatile uint8_t *bytes=reinterpret_cast<volatile uint8_t *>(db);
    if(bytes[4]!=0xdb || bytes[5]!=0x30 || bytes[6]!=0x03 || bytes[7]!=0x0c)return 0xffffffff;
    const unsigned data=8+8*16+8;size_t length=strlen(name);
    for(unsigned slave=0;slave<8;++slave) {
        unsigned header=8+slave*16;
        if(!x910_db16(bytes,header))break;
        unsigned entries=data+x910_db16(bytes,header+2),count=x910_db16(bytes,header+6);
        for(unsigned i=0;i<count && entries+i*24+24<=0x20000;++i) {
            unsigned at=entries+i*24;bool same=true;
            for(unsigned c=0;c<8 && same;++c)same=bytes[at+c]==(uint8_t)(c<length?name[c]:0);
            if(same)return (uint32_t)bytes[at+16]|(uint32_t)bytes[at+17]<<8|(uint32_t)bytes[at+18]<<16|(uint32_t)bytes[at+19]<<24;
        }
    }
    return 0;
}
// One active-only request on TCS 0: voltage (mV), mode, enable for one VRM resource, each response-required; returns
// after every command reports completion (CMD_STATUS bit 16) or 100 ms. Offsets: rpmh_rsc_reg_offset_ver_3_0 / ver_2_7.
static void x910_rpmh_ldo(volatile uint32_t *drv,bool v3,uint32_t address,uint32_t millivolts,volatile uint32_t *status) {
    volatile uint32_t *tcs=drv+0xd00/4;
    const unsigned stride=v3?24:20,wait=v3?0x20:0x10,control=v3?0x24:0x14,enable=v3?0x2c:0x1c;
    const unsigned msgid=v3?0x34:0x30,addr=v3?0x38:0x34,data=v3?0x3c:0x38,cstatus=v3?0x40:0x3c;
    const uint32_t value[3][2]={{0x0,millivolts},{0x8,7},{0x4,1}};
    tcs[control/4]=tcs[control/4]&~(1u<<24);tcs[control/4]=tcs[control/4]&~(1u<<16);
    tcs[enable/4]=0;tcs[wait/4]=0;
    for(unsigned j=0;j<3;++j) {
        tcs[(msgid+j*stride)/4]=8|(1u<<16)|(1u<<8);
        tcs[(addr+j*stride)/4]=address+value[j][0];
        tcs[(data+j*stride)/4]=value[j][1];
    }
    tcs[enable/4]=7;
    tcs[control/4]=1u<<16;tcs[control/4]=(1u<<16)|(1u<<24);
    for(unsigned t=0;t<10000;++t) {
        bool done=true;
        for(unsigned j=0;j<3;++j)done=done && (tcs[(cstatus+j*stride)/4]&(1u<<16));
        if(done)break;
        IODelay(10);
    }
    for(unsigned j=0;j<3;++j)status[j]=tcs[(cstatus+j*stride)/4];
    tcs[enable/4]=0;tcs[0x8/4]=1;   // IRQ_CLEAR for TCS 0
    tcs[control/4]=tcs[control/4]&~(1u<<24);tcs[control/4]=tcs[control/4]&~(1u<<16);
}
static void x910_touch_stage2(unsigned level) {
    X910Mmio vote={},rcg={},s_ahb={},tlmm24={},tlmm25={},cmddb={},rsc={};
    if(!x910_mmio_open(vote,0x152000) || !x910_mmio_open(rcg,0x118000) || !x910_mmio_open(s_ahb,0x123000)
        || !x910_mmio_open(tlmm24,0x0f118000) || !x910_mmio_open(tlmm25,0x0f119000)
        || (level>=3 && (!x910_mmio_open(cmddb,0x81c60000,0x20000) || !x910_mmio_open(rsc,0x17a20000,0x10000)))) {
        x910_touch_stage=29;
    } else {
        x910_touch_vote=vote.base[0x8/4];x910_touch_rcg[0]=rcg.base[0x4f0/4];x910_touch_rcg[1]=rcg.base[0x4f4/4];
        x910_touch_stage=20;x910_touch_save();
        if(level>=3) {
            x910_touch_ldo[0]=x910_cmd_db_addr(cmddb.base,"ldob14");x910_touch_ldo[1]=x910_cmd_db_addr(cmddb.base,"ldob12");
            uint32_t id=rsc.base[0];bool v3=((id>>16)&0xff)==3;
            volatile uint32_t *tcs=rsc.base+0xd00/4;
            x910_touch_rsc[0]=id;x910_touch_rsc[1]=tcs[(v3?0x28:0x18)/4];x910_touch_rsc[2]=tcs[(v3?0x2c:0x1c)/4];
            x910_touch_stage=24;x910_touch_save();
            bool found=x910_touch_ldo[0] && x910_touch_ldo[0]!=0xffffffff && x910_touch_ldo[1] && x910_touch_ldo[1]!=0xffffffff;
            if(found) {
                x910_rpmh_ldo(rsc.base,v3,x910_touch_ldo[1],1800,x910_touch_cmd);
                x910_rpmh_ldo(rsc.base,v3,x910_touch_ldo[0],3200,x910_touch_cmd+3);
                IOSleep(10);
            }
            x910_touch_stage=found?25:27;x910_touch_save();
            tlmm24.base[1]=0;tlmm24.base[0]=0x200;IOSleep(10);
        }
        tlmm24.base[1]=2;tlmm24.base[0]=0x200;
        unsigned previous=tlmm25.base[1]&1,highs=0,edges=0;
        for(unsigned i=0;i<6000;++i) {
            unsigned now=tlmm25.base[1]&1;
            if(now) {++highs;if(x910_touch_irq[0]==0xffffffff)x910_touch_irq[0]=i/2;}
            if(now!=previous)++edges;
            previous=now;IODelay(500);
        }
        x910_touch_irq[1]=highs;x910_touch_irq[2]=edges;x910_touch_irq[3]=previous;x910_touch_irq[4]=tlmm24.base[1];
        x910_touch_stage=21;x910_touch_save();
        vote.base[0x8/4]=vote.base[0x8/4]|(1u<<26)|(1u<<21);
        bool running=false;
        for(unsigned i=0;i<1000 && !running;++i) {
            x910_touch_clk[0]=rcg.base[0x4e4/4];x910_touch_clk[2]=s_ahb.base[0x280/4];
            bool hardware_gated=level>=3 && (x910_touch_clk[2]&2) && (vote.base[0x8/4]&(1u<<21));
            running=!(x910_touch_clk[0]>>31) && (hardware_gated || !(x910_touch_clk[2]>>31));
            if(!running)IODelay(10);
        }
        x910_touch_vote=vote.base[0x8/4];
        x910_touch_stage=running?22:28;x910_touch_save();
        if(running) {
            x910_touch_se[0]=x910_mmio_read(0x00a90000,0x68);
            x910_touch_se[1]=x910_mmio_read(0x00a90000,0x64);
            x910_touch_se[2]=x910_mmio_read(0x00a90000,0x40);
            x910_touch_se[3]=x910_mmio_read(0x00a90000,0xe24);
            x910_touch_stage=23;x910_touch_save();
        }
    }
    x910_mmio_close(vote);x910_mmio_close(rcg);x910_mmio_close(s_ahb);x910_mmio_close(tlmm24);x910_mmio_close(tlmm25);
    x910_mmio_close(cmddb);x910_mmio_close(rsc);
}
// x910-touch-probe=4, stage 4 (after stage 3 powered the GT9916 and C28 showed the SE4 runs I2C firmware 0x303 with its
// FIFO disabled): the first I2C transfer, through GPI_DMA1 as Linux does it (gpi.c, i2c-qcom-geni.c, qcom-geni-se.c; facts
// of the SoC interface only). Reads the controller's firmware version block, 32 bytes at 0x10014 (Berlin: 4-byte
// big-endian register address, then a repeated-start read); no controller register is written.
//  1. SE4 pins (GPIO 44 SDA, 45 SCL) in function 1 (qup1_se4), 2 mA, pull-up, as sm8550.dtsi.
//  2. SE4 in GPI mode: GENI IRQs cleared, SE_IRQ_EN 0, SE_GENI_DMA_MODE_EN 1, SE_GSI_EVENT_EN |= DMA_RX/TX, M, S.
//  3. The first GPII of 1-4 (dma-channel-mask 0x1e) whose event ring and both channels are not allocated. Register
//     block = 0xa00000 - 0x10000 (sm6350 layout). Event ring (256 x 16 bytes) allocated, then TX (chid 0, out) and RX
//     (chid 1, in) rings of 64 TREs, scratch pairing them to SE 4 with protocol 3 (I2C), then started. Commands are
//     polled through the context state field; the GPII interrupt is never serviced.
//  4. TX: CONFIG0 (400 kHz from 19.2 MHz: tlow 12, thigh 5, cycle 24, divider 2, packing), GO write to 0x5d with
//     stretch, DMA of the 4 address bytes (IEOT). After its completion event: GO read with LINK on TX and a 32-byte DMA
//     on RX (IEOT). Completion events (type 0x22, code 2 = EOT) are taken from the event ring by polling its RP.
// The ring memory is one physically contiguous buffer, flushed around every hand-over; its physical address goes to the
// GPI unchanged (no IOMapper on this platform). Every result is saved to the report file (TOUCH4 line). The channels
// stay started and the buffer is never freed: the GPI may still own it.
#include <IOKit/IOBufferMemoryDescriptor.h>
volatile uint32_t x910_touch4[12],x910_touch4_ev[8];
volatile uint8_t x910_touch4_rx[32];
static void x910_touch_line4(char *out,unsigned long capacity) {
    int n=snprintf(out,capacity,"TOUCH4 gpii=%u st=%x/%x/%x step=%u err=%x m_irq=%x geni=%x dma=%x/%x phys=%x%08x ev=%x/%x/%x/%x/%x/%x/%x/%x rx=",
      x910_touch4[0],x910_touch4[1],x910_touch4[2],x910_touch4[3],x910_touch4[4],x910_touch4[5],x910_touch4[6],x910_touch4[7],
      x910_touch4[8],x910_touch4[9],x910_touch4[10],x910_touch4[11],
      x910_touch4_ev[0],x910_touch4_ev[1],x910_touch4_ev[2],x910_touch4_ev[3],x910_touch4_ev[4],x910_touch4_ev[5],x910_touch4_ev[6],
      x910_touch4_ev[7]);
    unsigned long used=n>0?(unsigned long)n:0;
    for(unsigned i=0;i<32 && used+3<capacity;++i)used+=(unsigned long)snprintf(out+used,capacity-used,"%02x",x910_touch4_rx[i]);
}
static void x910_touch_save4(void) {
    static char text[1280],line[448],line4[400];uint64_t ns=0;absolutetime_to_nanoseconds(mach_absolute_time(),&ns);
    x910_touch_line2(line,sizeof(line));x910_touch_line4(line4,sizeof(line4));
    extern void x910_smmu_line_out(char *,unsigned long);
    static char smmu[200];x910_smmu_line_out(smmu,sizeof(smmu));
    int n=snprintf(text,sizeof(text),"X910REPORT-BEGIN round=0 uptime=%llu s touch\n%s\n%s\n%s\nX910REPORT-END\n",
      (unsigned long long)(ns/NSEC_PER_SEC),line,line4,smmu);
    if(n>0 && (unsigned)n<sizeof(text))x910_report_file_write(text,(unsigned long)n);
}
static inline volatile uint32_t &x910_reg(volatile uint32_t *base,uint32_t offset) {return base[offset/4];}
static inline void x910_dsb(void) {__asm__ volatile("dsb sy" ::: "memory");}
// C35 on hardware: the first GPI fetch faulted with an address size fault at the TX ring, so the page tables the SMMU walked were
// not the ones written. The rings and tables are RAM above 4 GiB, where sheng records that the kernel's cache-attribute path
// truncates the page number, and its touch DMA maintains the caches by VA instead. The same here, by VA.
static void x910_dc_civac(const volatile void *start,unsigned long bytes) {
    uint64_t ctr;
    __asm__ volatile("mrs %0, ctr_el0" : "=r"(ctr));
    const uintptr_t line=4u<<((ctr>>16)&15);
    const uintptr_t end=reinterpret_cast<uintptr_t>(start)+bytes;
    for(uintptr_t a=reinterpret_cast<uintptr_t>(start)&~(line-1);a<end;a+=line)__asm__ volatile("dc civac, %0" :: "r"(a) : "memory");
    x910_dsb();
}
// GPI register offsets relative to the GPI block (ee_base = block - 0x10000).
static constexpr uint32_t x910_gpi_ch(uint32_t n,uint32_t k) {return 0x20000-0x10000+0x4000*n+0x80*k;}
static constexpr uint32_t x910_gpi_ch_db(uint32_t n,uint32_t k) {return 0x22000-0x10000+0x4000*n+0x8*k;}
static constexpr uint32_t x910_gpi_ev(uint32_t n) {return 0x21000-0x10000+0x4000*n;}
static constexpr uint32_t x910_gpi_ev_db(uint32_t n) {return 0x22100-0x10000+0x4000*n;}
static constexpr uint32_t x910_gpi_gpii(uint32_t n,uint32_t offset) {return offset-0x10000+0x4000*n;}
static bool x910_gpi_wait_state(volatile uint32_t *gpi,uint32_t context,uint32_t state) {
    for(unsigned t=0;t<25000;++t) {
        if(((x910_reg(gpi,context)>>20)&0xf)==state)return true;
        IODelay(10);
    }
    return false;
}
struct X910Gpi {
    volatile uint32_t *gpi,*se;IOBufferMemoryDescriptor *buffer;uint8_t *memory;uint64_t physical;uint32_t n;unsigned seen;
};
static void x910_gpi_flush(X910Gpi &g) {x910_dc_civac(g.memory,0x4000);}
// Polls the event ring RP until `want` events have arrived (5 x 10 us x 50000 = 0.5 s); records the first four.
static bool x910_gpi_wait_events(X910Gpi &g,unsigned want) {
    for(unsigned t=0;t<50000;++t) {
        uint32_t rp=x910_reg(g.gpi,x910_gpi_ev(g.n)+0x10);
        unsigned have=(rp-(uint32_t)g.physical)/16;
        if(have>g.seen) {
            x910_gpi_flush(g);
            volatile uint32_t *event=reinterpret_cast<volatile uint32_t *>(g.memory);
            while(g.seen<have && g.seen<4) {
                x910_touch4_ev[g.seen*2]=event[g.seen*4+2];x910_touch4_ev[g.seen*2+1]=event[g.seen*4+3];++g.seen;
            }
        }
        if(g.seen>=want)return true;
        IODelay(10);
    }
    return false;
}
// C33 on hardware: all four GPIIs log NON_ALLOCATED_EVT_ACCESS and the channel errors, with the SMMU bypass confirmed. Two things
// still differ from gpi.c: it waits for each command's completion interrupt and clears it before going on (the state field may
// read the new value earlier). Commands are now acknowledged the same way, and the GPII error log and global status are kept
// after each step (GPISTEP line), which shows whether the error comes before or after the doorbell.
volatile uint32_t x910_gpi_step[4][12];   // per attempt: acks that timed out, phys low, then error log/global status pairs
static unsigned x910_gpi_attempt;
static void x910_gpi_mark(volatile uint32_t *gpi,uint32_t n,unsigned step) {
    x910_gpi_step[x910_gpi_attempt][2+step*2]=x910_reg(gpi,x910_gpi_gpii(n,0x23200));
    x910_gpi_step[x910_gpi_attempt][3+step*2]=x910_reg(gpi,x910_gpi_gpii(n,0x23100));
}
static void x910_gpi_ack(volatile uint32_t *gpi,uint32_t n,uint32_t source,uint32_t clear,uint32_t bit) {
    for(unsigned i=0;i<10000;++i) {
        if(x910_reg(gpi,x910_gpi_gpii(n,source))&bit) {
            x910_reg(gpi,x910_gpi_gpii(n,clear))=bit;x910_dsb();
            return;
        }
        IODelay(10);
    }
    x910_gpi_step[x910_gpi_attempt][0]=x910_gpi_step[x910_gpi_attempt][0]+1;
}
static uint32_t x910_touch_stage4_run(X910Gpi &g) {
    volatile uint32_t *gpi=g.gpi,*se=g.se;
    volatile uint32_t *tx=reinterpret_cast<volatile uint32_t *>(g.memory+0x1000);
    volatile uint32_t *rx=reinterpret_cast<volatile uint32_t *>(g.memory+0x1400);
    volatile uint8_t *txbuf=g.memory+0x2000;
    const uint64_t pev=g.physical,ptx=g.physical+0x1000,prx=g.physical+0x1400,ptxbuf=g.physical+0x2000,prxbuf=g.physical+0x2800;
    // 2. SE4 in GPI mode
    x910_reg(se,0x618)=0xffffffff;x910_reg(se,0x648)=0xffffffff;x910_reg(se,0xc44)=0xffffffff;x910_reg(se,0xd44)=0xffffffff;
    x910_reg(se,0xe1c)=0;x910_reg(se,0x258)=1;x910_reg(se,0xe18)=x910_reg(se,0xe18)|0xf;
    x910_touch4[4]=2;x910_touch_save4();
    // 3. GPII, event ring and channels
    for(uint32_t c=1;c<=4 && !g.n;++c)
        if(!((x910_reg(gpi,x910_gpi_ch(c,0))>>20)&0xf) && !((x910_reg(gpi,x910_gpi_ch(c,1))>>20)&0xf)
            && !((x910_reg(gpi,x910_gpi_ev(c))>>20)&0xf))g.n=c;
    const uint32_t n=g.n;x910_touch4[0]=n;
    if(!n)return 92;
    static const uint32_t masks[][2]={{0x23088,0x4f},{0x230b8,1},{0x23098,3},{0x2309c,1},{0x23108,0xf},{0x23120,0xf},
      {0x23188,0},{0x2318c,0},{0x23400,0},{0x23404,0},{0x23180,1},{0x23200,0}};
    for(auto &m:masks)x910_reg(gpi,x910_gpi_gpii(n,m[0]))=m[1];
    x910_reg(gpi,x910_gpi_gpii(n,0x23010))=0;                                           // EV ALLOCATE, chid 0
    if(!x910_gpi_wait_state(gpi,x910_gpi_ev(n),1))return 93;
    x910_gpi_ack(gpi,n,0x23094,0x230a4,1);
    x910_reg(gpi,x910_gpi_ev(n)+0x0)=16u<<24|1u<<16|2;
    x910_reg(gpi,x910_gpi_ev(n)+0x4)=4096;
    x910_reg(gpi,x910_gpi_ev(n)+0x8)=(uint32_t)pev;x910_reg(gpi,x910_gpi_ev(n)+0xc)=(uint32_t)(pev>>32);
    x910_reg(gpi,x910_gpi_ev_db(n)+4)=(uint32_t)(pev>>32);
    static const uint32_t cleared[]={0x20,0x28,0x2c,0x30,0x34};   // INT_MOD, MSI LSB/MSB, RP_UPDATE LSB/MSB
    for(uint32_t offset:cleared)x910_reg(gpi,x910_gpi_ev(n)+offset)=0;
    x910_gpi_flush(g);
    x910_reg(gpi,x910_gpi_ev_db(n))=(uint32_t)(pev+4096-16);
    x910_gpi_mark(gpi,n,0);
    for(uint32_t k=0;k<2;++k) {
        x910_reg(gpi,x910_gpi_gpii(n,0x23008))=k;                                       // CH ALLOCATE
        if(!x910_gpi_wait_state(gpi,x910_gpi_ch(n,k),1))return 94+k;
        x910_gpi_ack(gpi,n,0x23090,0x230a0,1u<<k);
        uint64_t ring=k?prx:ptx;
        x910_reg(gpi,x910_gpi_ch(n,k)+0x0)=16u<<24|(k?0u:1u)<<3|2;
        x910_reg(gpi,x910_gpi_ch(n,k)+0x4)=1024;
        x910_reg(gpi,x910_gpi_ch(n,k)+0x8)=(uint32_t)ring;x910_reg(gpi,x910_gpi_ch(n,k)+0xc)=(uint32_t)(ring>>32);
        x910_reg(gpi,x910_gpi_ch_db(n,k)+4)=(uint32_t)(ring>>32);
        x910_reg(gpi,x910_gpi_ch(n,k)+0x60)=(1u-k)<<16|3u<<4|4;                           // SCRATCH_0: pair, I2C, SE 4
        x910_reg(gpi,x910_gpi_ch(n,k)+0x64)=0;x910_reg(gpi,x910_gpi_ch(n,k)+0x68)=0;
        x910_reg(gpi,x910_gpi_ch(n,k)+0x6c)=0;x910_reg(gpi,x910_gpi_ch(n,k)+0x5c)=1;     // QOS
    }
    x910_dsb();
    x910_gpi_mark(gpi,n,1);
    for(uint32_t k=0;k<2;++k) {
        x910_reg(gpi,x910_gpi_gpii(n,0x23008))=1u<<24|k;                                 // CH START
        if(!x910_gpi_wait_state(gpi,x910_gpi_ch(n,k),2))return 96+k;
        x910_gpi_ack(gpi,n,0x23090,0x230a0,1u<<k);
    }
    x910_gpi_mark(gpi,n,2);
    x910_touch4[4]=3;x910_touch_save4();
    // 4. write the register address 0x00010014 (big-endian)
    txbuf[0]=0x00;txbuf[1]=0x01;txbuf[2]=0x00;txbuf[3]=0x14;
    tx[0]=12u|5u<<8|24u<<16|1u<<24|1u<<25;tx[1]=0;tx[2]=2;tx[3]=0x22u<<16|1u;              // CONFIG0, chain
    tx[4]=1u|0x5du<<8|1u<<26;tx[5]=0;tx[6]=4;tx[7]=0x20u<<16|1u;                            // GO write, stretch, chain
    tx[8]=(uint32_t)ptxbuf;tx[9]=(uint32_t)(ptxbuf>>32);tx[10]=4;tx[11]=0x10u<<16|1u<<9;    // DMA, IEOT
    x910_gpi_flush(g);
    x910_gpi_mark(gpi,n,3);
    x910_reg(gpi,x910_gpi_ch_db(n,0))=(uint32_t)(ptx+48);
    bool written=x910_gpi_wait_events(g,1);
    x910_gpi_mark(gpi,n,4);
    x910_touch4[8]=x910_reg(se,0xc40);x910_touch4[9]=x910_reg(se,0xd40);
    if(!written)return 98;
    if(((x910_touch4_ev[0]>>24)&0xff)!=2)return 88;
    x910_touch4[4]=4;x910_touch_save4();
    // read 32 bytes: GO read with LINK on TX, DMA on RX
    rx[0]=(uint32_t)prxbuf;rx[1]=(uint32_t)(prxbuf>>32);rx[2]=32;rx[3]=0x10u<<16|1u<<9;
    tx[12]=2u|0x5du<<8;tx[13]=0;tx[14]=32;tx[15]=0x20u<<16|1u<<11;
    x910_gpi_flush(g);
    x910_reg(gpi,x910_gpi_ch_db(n,1))=(uint32_t)(prx+16);
    x910_reg(gpi,x910_gpi_ch_db(n,0))=(uint32_t)(ptx+64);
    bool read=x910_gpi_wait_events(g,2);
    x910_gpi_flush(g);
    for(unsigned i=0;i<32;++i)x910_touch4_rx[i]=g.memory[0x2800+i];
    return read?5:99;
}
// x910-touch-probe=5: C29 reset the device on the first GPI doorbell (the report stopped at step 3, channels started):
// the GPI masters through the apps SMMU (0x15000000, stream 0xb6 in sm8550.dtsi), whose sCR0.USFCFG faults unmatched
// streams. C30/C31 gave the stream a bypass group of their own but left the context bank's CBA2R.VA64 clear, so the
// 0xa_xxxx_xxxx ring addresses were not reachable and the TX channel failed on its first fetch. The stream now goes through
// sheng's SmmuBypass.h (pinned sheng tree, same SM8550 apps SMMU, validated on hardware for its touch DMA): the last context
// bank as a shared bypass bank (SCTLR 0, CBAR S1-trans/S2-bypass, CBA2R.VA64), S2CR TRANSLATE to it before SMR, only a free
// group, existing mappings never changed, the DMA range checked against IDR2's widths, every write read back.
#include "SmmuBypass.h"
volatile uint32_t x910_smmu[16];   // scr0, id0, id1, id2, slot, bank, reuse, bank_ready, error, s2cr, smr, cba2r, pages, FSR, FAR, SCTLR
static const char *const x910_smmu_errors[]={"SMMU AArch64 stage 1 unsupported","DMA exceeds SMMU address width",
  "unsupported SMMU geometry","conflicting stream mapping","reserved context bank is not a bypass bank",
  "no free stream-match group","SMMU bypass bank readback","SMMU stream context readback","SMMU stream mapping readback"};
static void x910_smmu_line(char *out,unsigned long capacity) {
    snprintf(out,capacity,"SMMU scr0=%x id0=%x id1=%x id2=%x slot=%d bank=%u reuse=%u ready=%u err=%d s2cr=%x smr=%x cba2r=%x pages=%u fsr=%x far=%x sctlr=%x",
      x910_smmu[0],x910_smmu[1],x910_smmu[2],x910_smmu[3],(int)x910_smmu[4],x910_smmu[5],x910_smmu[6],x910_smmu[7],
      (int)x910_smmu[8],x910_smmu[9],x910_smmu[10],x910_smmu[11],x910_smmu[12],x910_smmu[13],x910_smmu[14],x910_smmu[15]);
}
void x910_smmu_line_out(char *out,unsigned long capacity);
void x910_smmu_line_out(char *out,unsigned long capacity) {x910_smmu_line(out,capacity);}
static uint32_t x910_smmu_rd(void *ctx,unsigned offset) {return static_cast<volatile uint32_t *>(ctx)[offset/4];}
static void x910_smmu_wr(void *ctx,unsigned offset,uint32_t value) {
    static_cast<volatile uint32_t *>(ctx)[offset/4]=value;__asm__ volatile("dsb sy" ::: "memory");
}
// Returns true when stream 0xb6 reaches [dma, dma+bytes) untranslated (already mapped to the bypass bank, or now).
static bool x910_smmu_bypass_gpi(uint64_t dma,uint64_t bytes) {
    const uint16_t sid=0xb6;
    X910Mmio smmu={};
    x910_smmu[8]=0xffffffff;
    if(!x910_mmio_open(smmu,0x15000000,0x100000)) {x910_smmu[8]=90;return false;}
    void *ctx=const_cast<uint32_t *>(smmu.base);
    x910_smmu[0]=x910_smmu_rd(ctx,0);x910_smmu[1]=x910_smmu_rd(ctx,0x20);x910_smmu[2]=x910_smmu_rd(ctx,0x24);
    x910_smmu[3]=x910_smmu_rd(ctx,0x28);
    struct smmu_io io={ctx,x910_smmu_rd,x910_smmu_wr};
    struct smmu_plan plan=smmu_plan_bypass(&io,sid,dma,bytes);
    x910_smmu[12]=plan.pages;x910_smmu[4]=plan.slot;x910_smmu[5]=plan.bank;x910_smmu[6]=(uint32_t)plan.reuse;x910_smmu[7]=(uint32_t)plan.bank_ready;
    const char *error=smmu_configure_bypass(&io,&plan,sid);
    if(error) {
        x910_smmu[8]=100;
        for(unsigned i=0;i<sizeof(x910_smmu_errors)/sizeof(x910_smmu_errors[0]);++i)
            if(!strcmp(error,x910_smmu_errors[i]))x910_smmu[8]=i;
    } else {
        x910_smmu[8]=0;
    }
    if(plan.slot<128) {x910_smmu[9]=x910_smmu_rd(ctx,0xc00+4*plan.slot);x910_smmu[10]=x910_smmu_rd(ctx,0x800+4*plan.slot);}
    x910_smmu[11]=x910_smmu_rd(ctx,0x1800+4*plan.bank);
    x910_mmio_close(smmu);
    return !error;
}
// C34 on hardware: the GPII error log stays clear through every command and appears with the first TX doorbell, and XNU has no
// memory below 4 GiB to give (the allocation fails). Linux never shows this engine a 36-bit ring: dma-iommu hands out low IOVAs,
// and Samsung's DT sets the pool at 0x100000. So the bypass bank from above now translates: 4 KiB granule, 36-bit input (the
// IDR2 IAS/UBS/OAS here), three levels, the rings at IOVA 0x100000 up, normal non-cacheable as the buffers are flushed by hand.
// Register fields: arm-smmu.h and the VMSAv8 format (facts of the architecture). FSR/FAR are kept for the report.
static IOBufferMemoryDescriptor *x910_smmu_tables;
static bool x910_smmu_translate_gpi(uint64_t iova,uint64_t physical,uint64_t bytes) {
    X910Mmio smmu={};
    if(!x910_mmio_open(smmu,0x15000000,0x100000))return false;
    volatile uint32_t *cb=smmu.base+(x910_smmu[12]+x910_smmu[5])*1024;
    const bool first=!x910_smmu_tables;
    if(first) {
        x910_smmu_tables=IOBufferMemoryDescriptor::inTaskWithPhysicalMask(kernel_task,
          (IOOptionBits)kIODirectionInOut|(IOOptionBits)kIOMemoryPhysicallyContiguous,0x3000,0x0000000ffffff000ULL);
        if(!x910_smmu_tables || x910_smmu_tables->prepare()!=kIOReturnSuccess) {x910_mmio_close(smmu);return false;}
        bzero(x910_smmu_tables->getBytesNoCopy(),0x3000);
    }
    volatile uint64_t *t=static_cast<volatile uint64_t *>(x910_smmu_tables->getBytesNoCopy());
    const uint64_t tables=x910_smmu_tables->getPhysicalSegment(0,nullptr,kIOMemoryMapperNone);
    t[0]=(tables+0x1000)|3;t[512+((iova>>21)&511)]=(tables+0x2000)|3;
    for(uint64_t o=0;o<bytes;o+=0x1000)t[1024+(((iova+o)>>12)&511)]=(physical+o)|1u<<10|2u<<8|1u<<6|3;   // AF, outer, RW, page
    x910_dc_civac(t,0x3000);
    if(first) {
        cb[0x0/4]=0;x910_dsb();
        cb[0x10/4]=7u<<15|1;                                // TCR2: SEP upstream, 36-bit PA
        cb[0x20/4]=(uint32_t)tables;cb[0x24/4]=(uint32_t)(tables>>32);
        cb[0x30/4]=2u<<30|1u<<23|28;                        // TCR: TG1 4K, EPD1, T0SZ 28; walks non-cacheable
        cb[0x34/4]=0;cb[0x38/4]=0x44;                       // ASID 0; MAIR attr 0 normal non-cacheable
        cb[0x58/4]=0xffffffff;x910_dsb();
        cb[0x0/4]=7;x910_dsb();                             // AFE, TRE, M
    } else {
        cb[0x610/4]=0;x910_dsb();
        cb[0x7f0/4]=0;
        for(unsigned i=0;i<1000 && (cb[0x7f4/4]&1);++i)IODelay(10);
    }
    x910_smmu[15]=cb[0x0/4];
    x910_mmio_close(smmu);
    return x910_smmu[15]==7;
}
static void x910_smmu_fault_read(void) {
    X910Mmio smmu={};
    if(!x910_mmio_open(smmu,0x15000000,0x100000))return;
    volatile uint32_t *cb=smmu.base+(x910_smmu[12]+x910_smmu[5])*1024;
    x910_smmu[13]=cb[0x58/4];x910_smmu[14]=cb[0x60/4];
    cb[0x58/4]=x910_smmu[13];x910_dsb();
    x910_mmio_close(smmu);
}
// C30 on hardware: with the SMMU bypass the device no longer resets, but the TX channel goes to state 0xf (error) with
// GPII error log 0x04006010 and no event. After stage 4 the full GPII state is kept for the report: both channel
// contexts and the event ring context (0x00-0x34), the GPII interrupt status words, the SE4 GENI/DMA/GSI registers and
// the first TX ring entries as written (GPIDUMP lines).
volatile uint32_t x910_gpi_dump[72];
volatile uint32_t x910_gpii_try[4][4];   // per attempt: GPII, step, error log, TX context 0
static void x910_gpi_dump_take(volatile uint32_t *gpi,volatile uint32_t *se,uint32_t n,const uint8_t *memory) {
    unsigned i=0;
    for(uint32_t k=0;k<2;++k)for(uint32_t o=0;o<0x38;o+=4)x910_gpi_dump[i++]=x910_reg(gpi,x910_gpi_ch(n,k)+o);
    for(uint32_t o=0;o<0x38;o+=4)x910_gpi_dump[i++]=x910_reg(gpi,x910_gpi_ev(n)+o);
    static const uint32_t gpii[]={0x23080,0x23090,0x23094,0x23100,0x23118,0x23200,0x23088,0x23098};
    for(uint32_t o:gpii)x910_gpi_dump[i++]=x910_reg(gpi,x910_gpi_gpii(n,o));
    static const uint32_t ser[]={0x40,0x610,0x640,0x258,0xe18,0xe1c,0xc40,0xd40,0x68,0x64,0xe24,0x48};
    for(uint32_t o:ser)x910_gpi_dump[i++]=x910_reg(se,o);
    const volatile uint32_t *tx=reinterpret_cast<const volatile uint32_t *>(memory+0x1000);
    for(unsigned w=0;w<6 && i<72;++w)x910_gpi_dump[i++]=tx[w];
    const volatile uint32_t *event=reinterpret_cast<const volatile uint32_t *>(memory);
    for(unsigned w=0;w<4 && i<72;++w)x910_gpi_dump[i++]=event[w];
}
static void x910_gpi_dump_line(char *out,unsigned long capacity) {
    int head=snprintf(out,capacity,"GPITRY %u:%u:%x:%x %u:%u:%x:%x %u:%u:%x:%x %u:%u:%x:%x\n",
      x910_gpii_try[0][0],x910_gpii_try[0][1],x910_gpii_try[0][2],x910_gpii_try[0][3],
      x910_gpii_try[1][0],x910_gpii_try[1][1],x910_gpii_try[1][2],x910_gpii_try[1][3],
      x910_gpii_try[2][0],x910_gpii_try[2][1],x910_gpii_try[2][2],x910_gpii_try[2][3],
      x910_gpii_try[3][0],x910_gpii_try[3][1],x910_gpii_try[3][2],x910_gpii_try[3][3]);
    unsigned long used=head>0?(unsigned long)head:0;
    for(unsigned a=0;a<4 && used+120<capacity;++a) {
        int n=snprintf(out+used,capacity-used,"GPISTEP %u %x",x910_gpi_step[a][0],x910_gpi_step[a][1]);
        if(n<=0)break;
        used+=(unsigned long)n;
        for(unsigned i=0;i<5 && used+24<capacity;++i)
            used+=(unsigned long)snprintf(out+used,capacity-used," %x/%x",x910_gpi_step[a][2+i*2],x910_gpi_step[a][3+i*2]);
        out[used++]='\n';out[used]=0;
    }
    for(unsigned i=0;i<72 && used+10<capacity;++i) {
        int n=snprintf(out+used,capacity-used,"%s%x",i%14==0?(i?"\nGPIDUMP ":"GPIDUMP "):" ",x910_gpi_dump[i]);
        if(n<=0)break;
        used+=(unsigned long)n;
    }
}
// C36/C37 on hardware: the first event arrives (transfer complete, code 0x10 UNEXP_ERR, status 4, TX channel), the SE's command stays
// active, and the device resets within seconds with the channels left as they are. So the whole state goes to the file at once,
// and the failed transfer is then stopped: channels stopped and reset (gpi_terminate_all), the SE's command cancelled, or aborted
// if the cancel is not acknowledged (geni-se.h). Each step is saved first, so the last GPIDOWN stage names what preceded a reset.
volatile uint32_t x910_gpi_down[5];   // stage, TX context, RX context, GENI status, M IRQ status
static void x910_touch_save_full(void) {
    static char text[3400],body[2800],line4[400];uint64_t ns=0;absolutetime_to_nanoseconds(mach_absolute_time(),&ns);
    x910_touch_line4(line4,sizeof(line4));x910_gpi_dump_line(body,sizeof(body));
    int n=snprintf(text,sizeof(text),"X910REPORT-BEGIN round=0 uptime=%llu s touch full\nGPIDOWN %u %x %x %x %x\n%s\n%s\nX910REPORT-END\n",
      (unsigned long long)(ns/NSEC_PER_SEC),x910_gpi_down[0],x910_gpi_down[1],x910_gpi_down[2],x910_gpi_down[3],x910_gpi_down[4],line4,body);
    if(n>0 && (unsigned)n<sizeof(text))x910_report_file_write(text,(unsigned long)n);
}
static void x910_gpi_stop(volatile uint32_t *gpi,volatile uint32_t *se,uint32_t n) {
    auto mark=[&](uint32_t stage) {
        x910_gpi_down[0]=stage;x910_gpi_down[1]=x910_reg(gpi,x910_gpi_ch(n,0));x910_gpi_down[2]=x910_reg(gpi,x910_gpi_ch(n,1));
        x910_gpi_down[3]=x910_reg(se,0x40);x910_gpi_down[4]=x910_reg(se,0x610);
        x910_touch_save_full();
    };
    mark(1);
    for(uint32_t k=0;k<2;++k) {
        x910_reg(gpi,x910_gpi_gpii(n,0x23008))=2u<<24|k;x910_dsb();                       // CH STOP
        x910_gpi_wait_state(gpi,x910_gpi_ch(n,k),3);
    }
    mark(2);
    for(uint32_t k=0;k<2;++k) {
        x910_reg(gpi,x910_gpi_gpii(n,0x23008))=9u<<24|k;x910_dsb();                       // CH RESET
        x910_gpi_wait_state(gpi,x910_gpi_ch(n,k),1);
    }
    mark(3);
    x910_reg(se,0x604)=1u<<2;x910_dsb();                                                  // M_GENI_CMD_CANCEL
    bool cancelled=false;
    for(unsigned t=0;t<10000 && !cancelled;++t) {cancelled=(x910_reg(se,0x610)&(1u<<4))!=0;if(!cancelled)IODelay(10);}
    mark(cancelled?4:5);
    if(!cancelled) {
        x910_reg(se,0x604)=1u<<1;x910_dsb();                                              // M_GENI_CMD_ABORT
        for(unsigned t=0;t<10000 && !(x910_reg(se,0x610)&(1u<<5));++t)IODelay(10);
        mark(6);
    }
    x910_reg(se,0x618)=0xffffffff;x910_dsb();
    mark(7);
}
static void x910_touch_stage4(unsigned level) {
    X910Mmio pin44={},pin45={},se={},gpi={};
    x910_touch4[4]=1;
    if(x910_mmio_open(pin44,0x0f12c000) && x910_mmio_open(pin45,0x0f12d000) && x910_mmio_open(se,0x00a90000,0x4000)
        && x910_mmio_open(gpi,0x00a00000,0x60000)) {
        // C31: GPII 1 rejected the first doorbell (NON_ALLOCATED_EVT_ACCESS, TX RP never moved) with everything else in
        // place. Each failed GPII keeps its channels and buffer, and the next free one is tried, up to four attempts.
        pin44.base[0]=0x7;pin45.base[0]=0x7;                                                   // 1. pins
        // C36 on hardware: with the caches maintained by VA the pass-through attempt reached the SE (GENI status 0x41, first time),
        // and the device reset on the doorbell of the second attempt, the low-IOVA one, with that command still active. One attempt
        // only until the first transfer is understood.
        for(unsigned attempt=0;attempt<1;++attempt) {
            IOBufferMemoryDescriptor *buffer=IOBufferMemoryDescriptor::inTaskWithPhysicalMask(kernel_task,
              (IOOptionBits)kIODirectionInOut|(IOOptionBits)kIOMemoryPhysicallyContiguous,0x4000,0x0000000ffffff000ULL);
            x910_gpi_attempt=attempt;
            if(!buffer || buffer->prepare()!=kIOReturnSuccess) {OSSafeReleaseNULL(buffer);x910_touch4[4]=91;break;}
            // First the pass-through bank, as sheng's touch DMA; only if that fails the same transfer through the low IOVAs.
            const uint64_t physical=buffer->getPhysicalSegment(0,nullptr,kIOMemoryMapperNone),iova=0x100000;
            X910Gpi g={gpi.base,se.base,buffer,static_cast<uint8_t *>(buffer->getBytesNoCopy()),attempt?iova:physical,0,0};
            x910_touch4[10]=(uint32_t)(physical>>32);x910_touch4[11]=(uint32_t)physical;
            x910_gpi_step[attempt][1]=(uint32_t)physical;
            bzero(g.memory,0x4000);
            for(unsigned e=0;e<8;++e)x910_touch4_ev[e]=0;
            if(level>=5 && (attempt?!x910_smmu_translate_gpi(iova,physical,0x4000):!x910_smmu_bypass_gpi(physical,0x4000))) {
                x910_touch4[4]=86;x910_touch_save4();break;
            }
            x910_touch4[4]=x910_touch_stage4_run(g);
            x910_smmu_fault_read();
            if(g.n) {
                x910_touch4[1]=x910_reg(gpi.base,x910_gpi_ch(g.n,0));x910_touch4[2]=x910_reg(gpi.base,x910_gpi_ch(g.n,1));
                x910_touch4[3]=x910_reg(gpi.base,x910_gpi_ev(g.n));x910_touch4[5]=x910_reg(gpi.base,x910_gpi_gpii(g.n,0x23200));
                x910_gpi_flush(g);x910_gpi_dump_take(gpi.base,se.base,g.n,g.memory);
                x910_touch_save_full();
            }
            x910_touch4[6]=x910_reg(se.base,0x610);x910_touch4[7]=x910_reg(se.base,0x40);
            x910_gpii_try[attempt][0]=g.n;x910_gpii_try[attempt][1]=x910_touch4[4];
            x910_gpii_try[attempt][2]=x910_touch4[5];x910_gpii_try[attempt][3]=x910_touch4[1];
            x910_touch_save4();
            if(g.n && x910_touch4[4]!=5)x910_gpi_stop(gpi.base,se.base,g.n);
            if(x910_touch4[4]==5 || !g.n)break;
        }
    } else {
        x910_touch4[4]=90;
    }
    x910_touch_save4();
    x910_mmio_close(pin44);x910_mmio_close(pin45);x910_mmio_close(se);x910_mmio_close(gpi);
}
// C24 hardware: with the probe run before launchd the boot froze on the Apple logo and launchd never logged, so it now
// runs from a thread call 120 s after Data (UI already up: a freeze leaves the lock screen visible) and in levels:
// 1 = GCC CBCRs and TLMM only, 2 = stage 2, 3 = stage 3, 4 = stage 4, 5 = stage 4 with the SMMU bypass (above). x910_touch_stage counts each completed read.
static unsigned x910_touch_level;
static void x910_touch_run(thread_call_param_t,thread_call_param_t) {
    static const uint32_t cbcr[5]={0x184e4,0x2327c,0x23280,0x23284,0x23294}; // s4, m_ahb, s_ahb, core, core_2x
    bool running=true;
    for(unsigned i=0;i<5;++i) {
        x910_touch_clk[i]=x910_mmio_read(0x100000+(cbcr[i]&~0xfffu),cbcr[i]&0xfff);
        if(x910_touch_clk[i]>>31)running=false;
        x910_touch_stage=2+i;
    }
    for(unsigned g=0;g<2;++g) {
        uint64_t page=0x0f100000+(uint64_t)(24+g)*0x1000;
        x910_touch_gpio[g*2]=x910_mmio_read(page,0);x910_touch_gpio[g*2+1]=x910_mmio_read(page,4);
        x910_touch_stage=7+g;
    }
    // SE4 words (GENI_FW_REVISION_RO 0x68, GENI_IF_DISABLE_RO 0x64, SE_GENI_STATUS 0x40, SE_HW_PARAM_0 0xe24) only from
    // stage 2, once its clocks are confirmed running.
    (void)running;
    if(x910_touch_level>=2)x910_touch_stage2(x910_touch_level);
    if(x910_touch_level>=4 && x910_touch_stage==23)x910_touch_stage4(x910_touch_level);
}
static void x910_touch_probe(void) {
    static thread_call_t call;
    if(!PE_parse_boot_argn("x910-touch-probe",&x910_touch_level,sizeof(x910_touch_level)) || !x910_touch_level
        || x910_touch_level>5 || call)return;
    call=thread_call_allocate(x910_touch_run,nullptr);if(!call)return;
    x910_touch_stage=1;
    uint64_t deadline=0;clock_interval_to_deadline(120,(uint32_t)NSEC_PER_SEC,&deadline);
    thread_call_enter_delayed(call,deadline);
}
// GPU stage (sheng-gpu-power=<n>, default off): sheng's staged Adreno probe (pinned GpuPowerProbe.cpp, unchanged) writes its
// result into the boot-progress extension rows 4-7 that sheng draws on screen. CONFIG_XNU_BOOT_PROGRESS is off here, so the
// rows are defined by this file and carried to the Data report instead. The probe is started 60 s after Data (the touch
// lesson of C24: no SoC block is touched before the userland is up); it waits 10 s itself. Every 2 s for 4 min the rows are
// written to the report file when they changed, so a hang leaves the last stage reached.
extern "C" {
char boot_progress_ext[8][48];
char boot_progress_ext_wide[2][112];
void sheng_gpu_power_probe(void);
extern volatile uint32_t native_gpu_power_stage;
// NativeAdrenoPublish hands backboardd's log export to sheng's UFS boot log (BootLog.c, sheng-log), which this kernel does not
// have: every piece is refused, as NativeAdreno does when the hook is absent.
int boot_log_export_put(uint32_t,uint32_t,uint32_t,const void *,uint32_t);
int boot_log_export_put(uint32_t,uint32_t,uint32_t,const void *,uint32_t) {return -1;}
}
static unsigned x910_gpu_mode,x910_gpu_ticks;
static void x910_gpu_rows(char *out,unsigned long capacity) {
    unsigned long used=(unsigned long)snprintf(out,capacity,"GPU mode=%u stage=%x t=%u",x910_gpu_mode,native_gpu_power_stage,
      x910_gpu_ticks);
    // Rows 4-7 are the GPU chain; the probe also uses rows 2-3 (IPI/GIC self-tests) from mode 8 on.
    for(unsigned r=0;r<8 && used<capacity;++r)
        if(boot_progress_ext[r][0])used+=snprintf(out+used,capacity-used,"\nGPU%u %.47s",r,boot_progress_ext[r]);
    for(unsigned r=0;r<2 && used<capacity;++r)
        if(boot_progress_ext_wide[r][0])used+=snprintf(out+used,capacity-used,"\nGPUW%u %.111s",r,boot_progress_ext_wide[r]);
}
static thread_call_t x910_gpu_call;
static void x910_gpu_tick(thread_call_param_t,thread_call_param_t) {
    static char text[1024],rows[800],last[800];
    if(!x910_gpu_ticks++)sheng_gpu_power_probe();
    x910_gpu_rows(rows,sizeof(rows));
    // The first line carries the tick count; compare from the rows on, and rewrite once a minute regardless.
    const char *body=strchr(rows,'\n'),*lastBody=strchr(last,'\n');
    if(!body || !lastBody || strcmp(body,lastBody) || x910_gpu_ticks%30==1) {
        uint64_t ns=0;absolutetime_to_nanoseconds(mach_absolute_time(),&ns);
        int n=snprintf(text,sizeof(text),"X910REPORT-BEGIN round=0 uptime=%llu s gpu\n%s\nX910REPORT-END\n",
          (unsigned long long)(ns/NSEC_PER_SEC),rows);
        if(n>0 && (unsigned)n<sizeof(text))x910_report_file_write(text,(unsigned long)n);
        strlcpy(last,rows,sizeof(last));
    }
    if(x910_gpu_ticks<120) {
        uint64_t deadline=0;clock_interval_to_deadline(2,(uint32_t)NSEC_PER_SEC,&deadline);
        thread_call_enter_delayed(x910_gpu_call,deadline);
    }
}
static void x910_gpu_probe(void) {
    if(!PE_parse_boot_argn("sheng-gpu-power",&x910_gpu_mode,sizeof(x910_gpu_mode)) || !x910_gpu_mode || x910_gpu_call)return;
    x910_gpu_call=thread_call_allocate(x910_gpu_tick,nullptr);if(!x910_gpu_call)return;
    uint64_t deadline=0;clock_interval_to_deadline(60,(uint32_t)NSEC_PER_SEC,&deadline);
    thread_call_enter_delayed(x910_gpu_call,deadline);
}
static void x910_keystore_snapshot(char *out,unsigned long capacity) {
    unsigned long used=strlen(out);if(used>=capacity)return;
    if(x910_touch_stage)
        used+=snprintf(out+used,capacity-used,"\nTOUCH lv=%u st=%u clk=%x/%x/%x/%x/%x gpio24=%x/%x gpio25=%x/%x se=%x/%x/%x/%x",
          x910_touch_level,x910_touch_stage,x910_touch_clk[0],x910_touch_clk[1],x910_touch_clk[2],x910_touch_clk[3],x910_touch_clk[4],
          x910_touch_gpio[0],x910_touch_gpio[1],x910_touch_gpio[2],x910_touch_gpio[3],
          x910_touch_se[0],x910_touch_se[1],x910_touch_se[2],x910_touch_se[3]);
    if(used+2<capacity && x910_touch_level>=2) {
        out[used++]='\n';out[used]=0;x910_touch_line2(out+used,capacity-used);used=strlen(out);
    }
    if(used+2<capacity && x910_touch_level>=4) {
        out[used++]='\n';out[used]=0;x910_touch_line4(out+used,capacity-used);used=strlen(out);
    }
    if(used+2<capacity && x910_touch_level>=5) {
        out[used++]='\n';out[used]=0;x910_smmu_line(out+used,capacity-used);used=strlen(out);
    }
    if(used+2<capacity && x910_touch_level>=5) {
        out[used++]='\n';out[used]=0;x910_gpi_dump_line(out+used,capacity-used);used=strlen(out);
    }
    if(used+2<capacity && x910_gpu_mode) {
        out[used++]='\n';out[used]=0;x910_gpu_rows(out+used,capacity-used);used=strlen(out);
    }
    if(used>=capacity)return;
    used+=snprintf(out+used,capacity-used,"\nSMP req=%u run=%u conduit=%u step=%u%u%u%u%u%u%u%u rc=%d/%d/%d/%d/%d/%d/%d",
      armcpu_smp_requested,armcpu_smp_running,armcpu_smp_conduit,armcpu_smp_step[0],armcpu_smp_step[1],armcpu_smp_step[2],
      armcpu_smp_step[3],armcpu_smp_step[4],armcpu_smp_step[5],armcpu_smp_step[6],armcpu_smp_step[7],
      armcpu_smp_psci_rc[1],armcpu_smp_psci_rc[2],armcpu_smp_psci_rc[3],armcpu_smp_psci_rc[4],armcpu_smp_psci_rc[5],
      armcpu_smp_psci_rc[6],armcpu_smp_psci_rc[7]);
    if(used<capacity)used+=snprintf(out+used,capacity-used,"\n%.119s stage=%u",native_cpufreq_stage?native_cpufreq_row:"CPUFREQ off",
      native_cpufreq_stage);
    if(used>=capacity)return;
    used+=snprintf(out+used,capacity-used,"\nAKS stage=%u st=%x opens=%u calls=%u refused=%u sel:",native_keyevents_stage,
      (unsigned)native_keyevents_status,native_aks_opens,native_aks_calls,native_keyevents_refused_opens);
    for(unsigned s=0;s<128 && used<capacity;++s)
        if(aksCount[s])used+=snprintf(out+used,capacity-used," %u:%u%s",s,aksCount[s],aksResult[s]?"e":"");
}

void native_legacy_display_probe(void);
void native_legacy_display_probe(void) {
    x910_ui_set_display_observer(x910_display_snapshot);
    // The registration path and optional original-userclient inspection both
    // reach this boot-thread entry. Run class constructors only once; a second
    // postModLoad would attempt to register the same metaclasses again.
    if(native_legacy_stage)return;
    native_legacy_stage=1;void *handle=OSMetaClass::preModLoad(g_kernel_kmod_info.name);
    if(!handle){native_legacy_display_checkpoint();return;}
    native_legacy_display_static_init();native_gop_presenter_static_init();native_legacy_meta_status=OSMetaClass::postModLoad(handle);
    if(native_legacy_meta_status){native_legacy_display_checkpoint();return;}
    PE_Video video={};native_legacy_stage=2;
    if(PE_current_console(&video) || !video.v_baseAddr || !video.v_width || !video.v_height || video.v_depth!=32
        || video.v_rotate || video.v_width>16384 || video.v_height>16384 || video.v_rowBytes<video.v_width*4
        || video.v_rowBytes>UINT64_MAX/video.v_height){native_legacy_display_checkpoint();return;}
    IOService *display=native_legacy_display_allocate();native_legacy_object=(uintptr_t)display;
    if(!display){native_legacy_display_checkpoint();return;}
    native_legacy_object_size=display->getMetaClass()->getClassSize();
    native_legacy_parent_match=(display->metaCast("IOMobileFramebufferLegacy") && display->metaCast("IOMobileFramebuffer") && display->metaCast("IOService"));
    native_legacy_stage=3;native_legacy_init_result=display->init();
    unsigned phase=1;PE_parse_boot_argn("offline-legacy-phase",&phase,sizeof(phase));
    if(native_legacy_init_result && (phase==2 || phase==3 || phase==4)) {
        // IOSurface matches the real IOBSD resource, published by bsd_autoconf.
        // Retain this actual object until that normal lifecycle boundary.
        pendingLegacyDisplay=display;return;
    }
    native_legacy_stage=7;display->release();native_legacy_released=1;native_legacy_stage=8;
    native_legacy_display_checkpoint();
}
void native_legacy_display_late_probe(void);
static bool x910_display_wait_for_data(void) {
    unsigned root=0;PE_parse_boot_argn("native-ufs-root",&root,sizeof(root));
    return root==1 && !x910_display_data_ready;
}
void native_legacy_display_late_probe(void) {
    // NativeRamBlock and IOSurface both match IOBSD. On the UFS main-system
    // route, let Data finish starting before waiting for the surface root.
    if(x910_display_wait_for_data()){x910_display_deferred=1;return;}

    IOService *display=pendingLegacyDisplay;if(!display)return;pendingLegacyDisplay=nullptr;
    native_legacy_surface_status=OSKextLoadKextWithIdentifier("com.apple.iokit.IOSurface");
    OSDictionary *matching=IOService::serviceMatching("IOSurfaceRoot");
    const uint64_t began=probeNowUs();
    IOService *surface=matching?IOService::waitForMatchingService(matching,30000000000ULL):nullptr;
    x910_surface_wait_us=probeNowUs()-began;
    native_legacy_surface_found=surface!=nullptr;OSSafeReleaseNULL(surface);OSSafeReleaseNULL(matching);
    PE_Video video={};
    if(!native_legacy_surface_status && native_legacy_surface_found && !PE_current_console(&video)
        && video.v_baseAddr && video.v_width && video.v_height && video.v_depth==32
        && !video.v_rotate && video.v_width<=16384 && video.v_height<=16384
        && video.v_rowBytes>=video.v_width*4 && video.v_rowBytes<=UINT64_MAX/video.v_height) {
        native_legacy_video_base=video.v_baseAddr;native_legacy_video_bytes=video.v_rowBytes*video.v_height;
        // A real firmware framebuffer resource, with addresses and extent
        // obtained from the actual GOP handoff. It has no invented compatible,
        // display list, panel timing, GPU/fence completion or vendor registers.
        native_legacy_stage=4;IOPlatformDevice *nub=new IOPlatformDevice;
        native_legacy_nub=(uintptr_t)nub;bool nubAttached=false,nubStarted=false,attached=false,started=false;
        IORegistryEntry *displayTreeParent=nullptr;bool dtAttached=false;
        IOService *parent=IOService::getResourceService();
        IODeviceMemory *memory=IODeviceMemory::withRange(video.v_baseAddr,video.v_rowBytes*video.v_height);
        OSArray *ranges=OSArray::withCapacity(1);
        if(nub && memory && ranges && parent && nub->init()) {
            ranges->setObject(memory);nub->setDeviceMemory(ranges);nub->setName("NativeGOPFramebuffer");
            nubAttached=nub->attach(parent);if(nubAttached)nubStarted=nub->start(parent);
            if(nubStarted) {
                unsigned phase=0;PE_parse_boot_argn("offline-legacy-phase",&phase,sizeof(phase));
                if(phase==4) {
                    // This is the actually implemented GOP display subsystem,
                    // attached to its real IOPlatformDevice memory resource.
                    // No Apple/vendor compatible or panel timing is supplied.
                    displayTreeParent=IORegistryEntry::fromPath("/arm-io",gIODTPlane);
                    const char type[]="display-subsystem";OSData *data=OSData::withBytes(type,sizeof(type));
                    if(displayTreeParent && data && nub->setProperty("device_type",data)) {
                        nub->setName("gop-display",gIODTPlane);dtAttached=nub->attachToParent(displayTreeParent,gIODTPlane);
                    }
                    OSSafeReleaseNULL(data);native_legacy_dt_attached=dtAttached;
                }
                if(phase==3) {
                    IOService *presenter=native_gop_presenter_create();bool presenterAttached=false;
                    if(presenter && presenter->init() && (presenterAttached=presenter->attach(nub)) && presenter->start(nub)) {
                        // Explicit own transport service only. The incomplete
                        // original framebuffer backend is never published.
                        activeGOPPresenter=presenter;activeGOPNub=nub;nub=nullptr;nubAttached=false;nubStarted=false;
                    } else {if(presenterAttached)presenter->detach(nub);OSSafeReleaseNULL(presenter);}
                } else {
                    SInt32 score=0;native_legacy_probe_match=(phase!=4 || dtAttached) && display->probe(nub,&score)==display;
                    if(native_legacy_probe_match)attached=display->attach(nub);
                    if(attached){native_legacy_stage=5;started=display->start(nub);native_legacy_start_result=started;}
                    if(started && phase==4) {
                        activeLegacyDisplay=display;activeGOPNub=nub;native_legacy_stage=10;
                        {
                            unsigned gpu=0;PE_parse_boot_argn("sheng-fb-gpu",&gpu,sizeof gpu);
                            if(gpu==1) {
                                IOService *handoff=native_gop_presenter_create();
                                // Not a child of the display subsystem's node: the original display code enumerates that node's children (int-075/077:
                                // SpringBoard found no display and crashed with the presenter attached there, registered or not).
                                IOService *home=IOService::getResourceService();
                                bool handoffAttached=false;
                                if(handoff && home)native_gop_presenter_set_source(handoff,nub);
                                if(handoff && home && handoff->init() && (handoffAttached=handoff->attach(home)) && handoff->start(home))gpuHandoffPresenter=handoff;
                                else {if(handoffAttached)handoff->detach(home);OSSafeReleaseNULL(handoff);}
                            }
                        }
                        finishUnitScalePolicy(true);
                        OSSafeReleaseNULL(displayTreeParent);
                        native_legacy_display_checkpoint();return;
                    }
                }
            }
        }
        native_legacy_stage=6;finishUnitScalePolicy(false);
        if(started)display->stop(nub);if(attached)display->detach(nub);
        if(dtAttached)nub->detachFromParent(displayTreeParent,gIODTPlane);
        if(nubStarted)nub->stop(parent);if(nubAttached)nub->detach(parent);
        OSSafeReleaseNULL(ranges);OSSafeReleaseNULL(memory);OSSafeReleaseNULL(nub);
        OSSafeReleaseNULL(displayTreeParent);
    }
    native_legacy_stage=7;display->release();native_legacy_released=1;native_legacy_stage=8;
    native_legacy_display_checkpoint();
}
}

extern "C" void x910_display_after_data(void) {
    x910_display_data_ready=1;
    // Before launchd and before the IOSurface wait: the keystore service must exist when the first daemon asks.
    x910_keystore_probe();
    x910_cpufreq_start();
    x910_touch_probe();
    x910_gpu_probe();
    native_legacy_display_late_probe();
}
