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
int x910_report_file_write(const char *text,unsigned long length) {
 vfs_context_t ctx=vfs_context_kernel();vnode_t vp=NULLVP;
 int error=vnode_open("/private/var/db/x910-report.txt",O_CREAT|O_TRUNC|O_NOFOLLOW|FWRITE,0644,0,&vp,ctx);
 if(error)return error;
 error=vn_rdwr(UIO_WRITE,vp,(caddr_t)(uintptr_t)text,(int)length,0,UIO_SYSSPACE,IO_NOAUTH|IO_SYNC,
  vfs_context_ucred(ctx),NULL,vfs_context_proc(ctx));
 int closed=vnode_close(vp,FWRITE,ctx);
 return error?error:closed;
}
static void x910_ui_exit_probe(int pid, const char *name, int rv,
    unsigned ns, uint64_t code, const char *description) {
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
