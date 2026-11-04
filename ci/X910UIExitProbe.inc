// SPDX-License-Identifier: MIT
// Explicit bring-up checkpoint on the first selected UI process exit.
// Called from proc_prepareexit, before teardown; no Apple executable changes.
extern int proc_selfpid(void);
extern volatile int native_mobile_graphics_load_status;
typedef void (*x910_display_observer_t)(char *,unsigned long);
static x910_display_observer_t x910_display_observer;
void x910_ui_set_display_observer(x910_display_observer_t observer);
void x910_ui_set_display_observer(x910_display_observer_t observer) {
 __atomic_store_n(&x910_display_observer,observer,__ATOMIC_RELEASE);
}
// Process lookup for the timed report's stack capture (osfmk side): a proc reference keeps the task.
void *x910_proc_task_ref(int pid,task_t *task);
void *x910_proc_task_ref(int pid,task_t *task) {
 proc_t p=proc_find(pid);
 *task=p?proc_task(p):TASK_NULL;
 return p;
}
void x910_proc_task_release(void *p);
void x910_proc_task_release(void *p) {proc_rele((proc_t)p);}
void x910_proc_name(int pid,char *buf,int size);
void x910_proc_name(int pid,char *buf,int size) {proc_name(pid,buf,size);}
// The same display snapshot for the timed userland report, which runs while SpringBoard is alive.
void x910_ui_display_snapshot(char *out,unsigned long capacity);
void x910_ui_display_snapshot(char *out,unsigned long capacity) {
 x910_display_observer_t observer=__atomic_load_n(&x910_display_observer,__ATOMIC_ACQUIRE);
 if(observer)observer(out,capacity);
}
// x910-userland-probe-file=1: with 8 CPUs and SpringBoard drawing, the report's panic screen never appeared on hardware
// (C25), so the timed report is written instead to /private/var/db/x910-report.txt on Data, read back from TWRP.
int x910_report_to_file(void);
int x910_report_to_file(void) {
 int mode=0;
 return PE_parse_boot_argn("x910-userland-probe-file",&mode,sizeof(mode)) && mode==1;
}
int x910_report_file_write(const char *text,unsigned long length);
volatile unsigned x910_report_phase, x910_report_round, x910_msg_phase;
static int x910_named_report_write(const char *path,const char *text,unsigned long length) {
 vfs_context_t ctx=vfs_context_kernel();vnode_t vp=NULLVP;
 int error=vnode_open(path,O_CREAT|O_TRUNC|O_NOFOLLOW|FWRITE,0644,0,&vp,ctx);
 if(error)return error;
 error=vn_rdwr(UIO_WRITE,vp,(caddr_t)(uintptr_t)text,(int)length,0,UIO_SYSSPACE,IO_NOAUTH|IO_SYNC,
  vfs_context_ucred(ctx),NULL,vfs_context_proc(ctx));
 int closed=vnode_close(vp,FWRITE,ctx);
 return error?error:closed;
}
int x910_pac_report_file_write(const char *,unsigned long);
int x910_pac_report_file_write(const char *text,unsigned long length) {
 return x910_named_report_write("/private/var/db/x910-pac.txt",text,length);
}
int x910_gpu_report_file_write(const char *text,unsigned long length);
int x910_gpu_report_file_write(const char *text,unsigned long length) {
 return x910_named_report_write("/private/var/db/x910-gpu.txt",text,length);
}
int x910_report_file_write(const char *text,unsigned long length) {
 vfs_context_t ctx=vfs_context_kernel();vnode_t vp=NULLVP;
 int error=vnode_open("/private/var/db/x910-report.txt",O_CREAT|O_TRUNC|O_NOFOLLOW|FWRITE,0644,0,&vp,ctx);
 if(error)return error;
 error=vn_rdwr(UIO_WRITE,vp,(caddr_t)(uintptr_t)text,(int)length,0,UIO_SYSSPACE,IO_NOAUTH|IO_SYNC,
  vfs_context_ucred(ctx),NULL,vfs_context_proc(ctx));
 int closed=vnode_close(vp,FWRITE,ctx);
 return error?error:closed;
}
// V45 (x910-test bit 2): MobileSafari's failing system calls, with the path when the call takes one, dumped when it exits.
// Observation only: results and errors are returned unchanged. Successful path calls naming SafariCore are kept too,
// to show which bundle directory the process really uses.
extern uint64_t mach_absolute_time(void);
extern void absolutetime_to_nanoseconds(uint64_t,uint64_t *);
#define X910_SC_N 192
struct x910_sc_entry { uint32_t ms; uint16_t code; int16_t error; char path[120]; };
static struct x910_sc_entry x910_sc_ring[X910_SC_N];
static volatile uint32_t x910_sc_next;
static volatile int x910_sc_mode=-1;
static int x910_sc_path_arg(unsigned code) {
 switch(code){
 case 5:case 10:case 12:case 33:case 58:case 136:case 157:case 188:case 190:case 191:case 216:case 220:case 234:
 case 240:case 242:case 338:case 340:case 341:case 342:case 345:case 398:case 441:return 0;
 case 218:case 463:case 464:case 466:case 469:case 470:case 472:case 476:return 1;
 default:return -1;
 }
}
static bool x910_sc_contains(const char *text,const char *word) {
 for(;*text;++text){const char *a=text,*b=word;while(*a && *b && *a==*b){++a;++b;}if(!*b)return true;}
 return false;
}
void x910_syscall_observe(void *proc,unsigned code,const uint64_t *args,int error);
void x910_syscall_observe(void *proc,unsigned code,const uint64_t *args,int error) {
 int mode=__atomic_load_n(&x910_sc_mode,__ATOMIC_RELAXED);
 if(__probable(mode==0))return;
 if(mode<0){int test=0;PE_parse_boot_argn("x910-test",&test,sizeof(test));mode=(test&4)?1:0;__atomic_store_n(&x910_sc_mode,mode,__ATOMIC_RELAXED);if(!mode)return;}
 if(!proc || strcmp(proc_best_name((proc_t)proc),"MobileSafari"))return;
 if(error==4 || error==35 || error==60 || error==-1 || error==-2)return; // EINTR, EAGAIN, ETIMEDOUT, ERESTART, EJUSTRETURN
 const int which=x910_sc_path_arg(code);
 char path[192]="";
 if(which>=0){size_t done=0;int copied=copyinstr((user_addr_t)args[which],path,sizeof(path)-1,&done);path[sizeof(path)-1]=0;if(copied && copied!=ENAMETOOLONG)strlcpy(path,"?",sizeof(path));}
 if(!error && !(which>=0 && x910_sc_contains(path,"SafariCore")))return;
 struct x910_sc_entry *e=&x910_sc_ring[__atomic_fetch_add(&x910_sc_next,1,__ATOMIC_RELAXED)%X910_SC_N];
 uint64_t ns=0;absolutetime_to_nanoseconds(mach_absolute_time(),&ns);
 e->ms=(uint32_t)(ns/1000000);e->code=(uint16_t)code;e->error=(int16_t)error;
 size_t length=strlen(path);
 // Keep the end of a long path: the file name is what identifies the resource.
 strlcpy(e->path,length<sizeof(e->path)?path:path+length-(sizeof(e->path)-1),sizeof(e->path));
}
static int x910_named_report_write(const char *path,const char *text,unsigned long length);
static void x910_sc_dump(int pid) {
 static char text[3800];
 const uint32_t total=__atomic_load_n(&x910_sc_next,__ATOMIC_ACQUIRE),count=total<X910_SC_N?total:X910_SC_N,first=total-count;
 for(unsigned page=0;page*24<count && page<8;++page){
  uint64_t ns=0;absolutetime_to_nanoseconds(mach_absolute_time(),&ns);
  int used=snprintf(text,sizeof(text),"X910REPORT-BEGIN round=%u uptime=%llu s syscalls build=v45 pid=%d total=%u\n",
   1460+page,(unsigned long long)(ns/NSEC_PER_SEC),pid,total);
  for(unsigned k=page*24;k<count && k<page*24+24 && used<(int)sizeof(text)-180;++k){
   const struct x910_sc_entry *e=&x910_sc_ring[(first+k)%X910_SC_N];
   used+=snprintf(text+used,sizeof(text)-(size_t)used,"%u %u e%d %s\n",e->ms,e->code,e->error,e->path);
  }
  used+=snprintf(text+used,sizeof(text)-(size_t)used,"X910REPORT-END\n");
  char file[48];snprintf(file,sizeof(file),"/private/var/db/x910-sc-%u.txt",page);
  x910_named_report_write(file,text,(unsigned long)used);
 }
}
// x910-exit-log=1: the fault record of an exiting backboardd or SpringBoard, written to Data without stopping the system (the
// x910-ui-exit checkpoint panics, so SpringBoard never comes back). The first 8 exits, each in its own file, in the report's format.
static void x910_exit_log(int pid, const char *name, int rv, unsigned ns, uint64_t code, const char *description) {
 int mode=0;
 if (!PE_parse_boot_argn("x910-exit-log", &mode, sizeof(mode)) || mode!=1) return;
 // iconservicesagent crash-loops every 10 s when the plugin is present (C50): its first two exits only, in their own numbers.
 const bool icons=!strcmp(name,"iconservicesagent"),widget=!strncmp(name,"WidgetRenderer_D",16);
 // Safari and its WebKit processes too (2026-11-03: MobileSafari crashed on every launch, with nothing in the message buffer).
 const bool safari=!strcmp(name,"MobileSafari") || !strncmp(name,"com.apple.WebKit",16);
 if (strcmp(name,"SpringBoard") && strcmp(name,"backboardd") && !icons && !widget && !safari) return;
 static int busy; static unsigned count,iconCount,widgetCount,safariCount; static char text[2400];
 if (__atomic_exchange_n(&busy,1,__ATOMIC_ACQUIRE)) return;
 // Safari's exits rotate through four numbers of their own (30..33), so they are kept whenever they happen.
 unsigned n=safari?30+safariCount++%4:widget?20+widgetCount++:icons?10+iconCount++:count++;
 if (safari || (widget?n<22:icons?n<12:n<8)) {
  extern uint64_t mach_absolute_time(void);
  extern void absolutetime_to_nanoseconds(uint64_t,uint64_t *);
  uint64_t now=0;absolutetime_to_nanoseconds(mach_absolute_time(),&now);
  snprintf(text,sizeof(text),"X910REPORT-BEGIN round=%u uptime=%llu s exit\n%s[%d] rv=%x ns=%u code=%llx\nreason: %.300s",
   900+n,(unsigned long long)(now/NSEC_PER_SEC),name,pid,(unsigned)rv,ns,(unsigned long long)code,description?description:"unavailable");
  extern void x910_fault_critical_exit(int,char *,unsigned long);
  x910_fault_critical_exit(pid,text,sizeof(text)-32);
  strlcat(text,"\nX910REPORT-END\n",sizeof(text));
  char path[48];snprintf(path,sizeof(path),"/private/var/db/x910-exit-%u.txt",n);
  vfs_context_t ctx=vfs_context_kernel();vnode_t vp=NULLVP;
  if (!vnode_open(path,O_CREAT|O_TRUNC|O_NOFOLLOW|FWRITE,0644,0,&vp,ctx)) {
   vn_rdwr(UIO_WRITE,vp,(caddr_t)(uintptr_t)text,(int)strlen(text),0,UIO_SYSSPACE,IO_NOAUTH|IO_SYNC,
    vfs_context_ucred(ctx),NULL,vfs_context_proc(ctx));
   vnode_close(vp,FWRITE,ctx);
  }
 }
 if(!strcmp(name,"MobileSafari"))x910_sc_dump(pid);
 __atomic_store_n(&busy,0,__ATOMIC_RELEASE);
}
// V36: independent files for GPU ticks and timed reports; record every potentially blocking report step.
#include <sys/mount.h>
#include <sys/vnode.h>
static unsigned long long x910_report_uptime(void) {
 uint64_t ns=0;absolutetime_to_nanoseconds(mach_absolute_time(),&ns);return ns/NSEC_PER_SEC;
}
static int x910_mount_report(mount_t mp,void *argument) {
 char *text=argument;size_t used=strlen(text);struct vfsstatfs *st=vfs_statfs(mp);
 if(st && used<3400)snprintf(text+used,3800-used,"%s <- %s type=%s flags=%llx\n",
  st->f_mntonname,st->f_mntfromname,st->f_fstypename,(unsigned long long)vfs_flags(mp));
 return VFS_RETURNED;
}
void x910_paths_report(void);
void x910_paths_report(void) {
 static const char *paths[]={"/private/preboot/Cryptexes/OS","/System/Cryptexes/OS",
  "/System/Library/PrivateFrameworks/SafariShared.framework",
  "/System/Library/PrivateFrameworks/SafariShared.framework/SitesAllowedToAutoplay.plist",
  "/private/preboot/Cryptexes/OS/System/Library/PrivateFrameworks/SafariShared.framework/SitesAllowedToAutoplay.plist",
  "/System/Library/Frameworks/WebKit.framework",
  "/private/preboot/Cryptexes/OS/System/Library/Frameworks/WebKit.framework/Info.plist",
  "/System/Library/PrivateFrameworks/SafariShared.framework/UserAgentQuirks.plist",
  "/System/Library/PrivateFrameworks/SafariShared.framework/WBSDuckDuckGoTDS.json",
  "/System/Library/PrivateFrameworks/SafariCore.framework/Info.plist",
  "/System/Library/PrivateFrameworks/SafariCore.framework/WBSPasswordWarningTopFraudTargets.plist",
  "/private/preboot/Cryptexes/OS/System/Library/PrivateFrameworks/SafariCore.framework/Info.plist",
  "/private/preboot/Cryptexes/OS/System/Library/PrivateFrameworks/SafariCore.framework/WBSPasswordWarningTopFraudTargets.plist"};
 static char text[4000];
 for(unsigned i=0;i<sizeof(paths)/sizeof(paths[0]);++i) {
  x910_report_phase=20+i;
  vnode_t vp=NULLVP;int error=vnode_lookup(paths[i],0,&vp,vfs_context_kernel());
  char resolved[1024]="";int size=sizeof(resolved),pe=-1,type=-1;
  if(!error){pe=vn_getpath(vp,resolved,&size);type=vnode_vtype(vp);vnode_put(vp);}
  unsigned char bytes[16]={0};int opened=-1,read_error=-1,resid=16;
  if(!error && type==VREG) {
   vnode_t file=NULLVP;vfs_context_t ctx=vfs_context_kernel();
   opened=vnode_open(paths[i],FREAD|O_NOFOLLOW,0,0,&file,ctx);
   if(!opened){read_error=vn_rdwr(UIO_READ,file,(caddr_t)bytes,16,0,UIO_SYSSPACE,IO_NOAUTH,vfs_context_ucred(ctx),&resid,vfs_context_proc(ctx));vnode_close(file,FREAD,ctx);}
  }
  int n=snprintf(text,sizeof(text),"X910REPORT-BEGIN round=%u uptime=%llu s paths build=v45\npath=%s lookup=%d type=%d getpath=%d resolved=%s\nopen=%d read=%d resid=%d head=%02x%02x%02x%02x%02x%02x%02x%02x\nX910REPORT-END\n",
   1400+i,x910_report_uptime(),paths[i],error,type,pe,resolved,opened,read_error,resid,bytes[0],bytes[1],bytes[2],bytes[3],bytes[4],bytes[5],bytes[6],bytes[7]);
  char file[64];snprintf(file,sizeof(file),"/private/var/db/x910-path-%u.txt",i);
  x910_named_report_write(file,text,n);
 }
 snprintf(text,sizeof(text),"X910REPORT-BEGIN round=1450 uptime=%llu s mounts build=v45\n",x910_report_uptime());
 x910_report_phase=28;vfs_iterate(0,x910_mount_report,text);
 strlcat(text,"X910REPORT-END\n",sizeof(text));
 x910_named_report_write("/private/var/db/x910-mounts.txt",text,strlen(text));
}
#include <sys/msgbuf.h>
void x910_msgbuf_files(unsigned round);
void x910_msgbuf_files(unsigned round) {
 int mode=0;
 if (!PE_parse_boot_argn("x910-msgbuf-file",&mode,sizeof(mode)) || mode!=1) return;
 static char text[4000];
 // Save WiFi first: a blocked message-buffer file must not hide the firmware failure.
 extern char native_wifi_text[512][112],native_wifi_note[128][112],native_wifi_first[256][112];
 extern volatile uint32_t native_wifi_text_n,native_wifi_note_n;
 const unsigned total=native_wifi_text_n,notes=native_wifi_note_n;
 for(unsigned page=0;page<32;++page) {
  const bool early=page>=23,note=page>=18 && !early;unsigned count=early?(total<256?total:256):note?notes:total,limit=early?256:note?128:512;
  unsigned first=(early?page-23:note?page-18:page)*30;
  if(first>=count || first>=limit)continue;
  x910_msg_phase=100+page;
  int used=snprintf(text,sizeof(text),"X910REPORT-BEGIN round=%u uptime=%llu s wifi build=v45 report=%u total=%u notes=%u\n",
   1300+page,x910_report_uptime(),round,total,notes);
  unsigned base=count>limit?count-limit:0;
  for(unsigned k=first;k<first+30 && base+k<count && k<limit;++k)
   used+=snprintf(text+used,sizeof(text)-(size_t)used,"%.110s\n",early?native_wifi_first[(base+k)%256]:note?native_wifi_note[(base+k)%128]:native_wifi_text[(base+k)%512]);
  used+=snprintf(text+used,sizeof(text)-(size_t)used,"X910REPORT-END\n");
  char file[64];snprintf(file,sizeof(file),"/private/var/db/x910-wifi-%u.txt",page);
  x910_named_report_write(file,text,used);
 }
 if(!msgbufp || !msgbufp->msg_bufc || msgbufp->msg_size<=0)return;
 const int size=msgbufp->msg_size,chunk=3600,newest=msgbufp->msg_bufx;
 for(int n=0;n<(round==1?32:16);++n) {
  const int back=(16-n%16)*chunk;if(back>size)continue;
  int at=newest-back;if(at<0)at+=size;
  x910_msg_phase=200+n;
  int used=snprintf(text,sizeof(text),"X910REPORT-BEGIN round=%d uptime=%llu s msg build=v45 report=%u\n",
   n<16?1000+n:1100+n-16,x910_report_uptime(),round);
  for(int i=0;i<chunk && used<(int)sizeof(text)-32;++i){char c=msgbufp->msg_bufc[(at+i)%size];text[used++]=c?c:' ';}
  used+=snprintf(text+used,sizeof(text)-(size_t)used,"\nX910REPORT-END\n");
  char file[64];snprintf(file,sizeof(file),"/private/var/db/x910-msg-%d.txt",n);
  x910_named_report_write(file,text,used);
 }
 x910_msg_phase=0;
}
static void x910_ui_exit_probe(int pid, const char *name, int rv,
    unsigned ns, uint64_t code, const char *description) {
 x910_exit_log(pid,name,rv,ns,code,description);
 int selected=0;
 if (!PE_parse_boot_argn("x910-ui-exit", &selected, sizeof(selected))) return;
 if (strcmp(name,"SpringBoard") && !(selected==2 && !strcmp(name,"backboardd"))) return;
 if (selected!=1 && selected!=2) return;
 // Another task's address space must never be read by the fault formatter.
 // The formatter itself checks proc_selfpid before attempting copyin.
 char report[1800];
 snprintf(report,sizeof(report),
  "X910 UI EXIT (intentional checkpoint)\n%s[%d] rv=%x ns=%u code=%llx self=%d\nreason: %.300s",
  name,pid,(unsigned)rv,ns,(unsigned long long)code,proc_selfpid(),
  description ? description : "unavailable");
 unsigned long used=strlen(report);
 x910_display_observer_t observer=__atomic_load_n(&x910_display_observer,__ATOMIC_ACQUIRE);
 snprintf(report+used,sizeof(report)-used,"\nDISPLAY family_load=%d observer=%u",native_mobile_graphics_load_status,observer?1U:0U);
 if(observer)observer(report,sizeof(report));
 extern void x910_fault_critical_exit(int,char *,unsigned long);
 x910_fault_critical_exit(pid,report,sizeof(report));
 panic("%s",report);
}
