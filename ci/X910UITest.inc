// Opt-in UI smoke test through the existing digitizer seqlock. No hardware
// registers or Apple executables are changed. x910-test bit 1 enables it.
// Coordinates are normalized from the observed iPadOS17 lock/home layout.
static uint32_t x910_ui_test_mode;
static bool x910_ui_test_parsed;
static uint64_t x910_ui_test_started_ns;
static uint32_t x910_ui_test_reports_before;
volatile uint32_t x910_ui_test_stage;

static void x910_ui_test_contact(bool down,int x,int y) {
    native_touch_seq=native_touch_seq+1;
    __asm__ volatile("dmb ish" ::: "memory");
    if(down) {
        native_touch_ids[0]=0;
        native_touch_xs[0]=x;
        native_touch_ys[0]=y;
    }
    native_touch_count=down?1:0;
    __asm__ volatile("dmb ish" ::: "memory");
    native_touch_seq=native_touch_seq+1;
}

static void x910_ui_test_tick(void) {
    if(!x910_ui_test_parsed) {
        PE_parse_boot_argn("x910-test",&x910_ui_test_mode,sizeof(x910_ui_test_mode));
        x910_ui_test_parsed=true;
    }
    if(!(x910_ui_test_mode&2) || x910_ui_test_stage>=4)return;
    uint64_t ns=0;absolutetime_to_nanoseconds(mach_absolute_time(),&ns);
    if(ns<210ULL*NSEC_PER_SEC)return;
    // A real touch owns the stream. Its hardware frame was just published by
    // this same polling thread, so leave that frame intact and cancel the test.
    if(x910_touch6[9]) {x910_ui_test_stage=9;printf("x910 ui test cancelled: physical contact\n");return;}
    if(!x910_ui_test_stage) {
        if(native_hid_stage!=3 || x910_touch6[0]!=3) {
            if(ns>260ULL*NSEC_PER_SEC)x910_ui_test_stage=8;
            return;
        }
        x910_ui_test_reports_before=native_touch_hid_reports;
        x910_ui_test_started_ns=ns;
        x910_ui_test_stage=1;
        printf("x910 ui test: unlock swipe start\n");
    }
    uint64_t elapsed=(ns-x910_ui_test_started_ns)/NSEC_PER_MSEC;
    if(x910_ui_test_stage==1) {
        if(elapsed<600) {
            // Bottom center to upper middle; 600 ms, one contact.
            int y=19913-(int)(elapsed*14833/600);
            x910_ui_test_contact(true,15240,y);
        } else {
            x910_ui_test_contact(false,0,0);
            x910_ui_test_stage=2;
            printf("x910 ui test: unlock swipe released\n");
        }
    } else if(x910_ui_test_stage==2 && elapsed>=6600) {
        // Safari is the left-most icon in the observed home dock.
        x910_ui_test_contact(true,10363,18430);
        x910_ui_test_stage=3;
        printf("x910 ui test: Safari dock press\n");
    } else if(x910_ui_test_stage==3 && elapsed>=6740) {
        x910_ui_test_contact(false,0,0);
        x910_ui_test_stage=4;
        printf("x910 ui test: Safari dock released reports=%u->%u\n",x910_ui_test_reports_before,native_touch_hid_reports);
    }
}
