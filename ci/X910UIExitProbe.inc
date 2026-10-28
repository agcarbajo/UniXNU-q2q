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
// The same display snapshot for the timed userland report, which runs while SpringBoard is alive.
void x910_ui_display_snapshot(char *out,unsigned long capacity);
void x910_ui_display_snapshot(char *out,unsigned long capacity) {
 x910_display_observer_t observer=__atomic_load_n(&x910_display_observer,__ATOMIC_ACQUIRE);
 if(observer)observer(out,capacity);
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
