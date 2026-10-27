// SPDX-License-Identifier: MIT
// Opt-in observation only: never claims a signature check is a running UI.
#include <kern/clock.h>
#include <kern/thread.h>
#include <kern/sched_prim.h>
#include <vm/vm_page.h>
#include <string.h>
struct x910_probe_event { int pid, error; unsigned ns; uint64_t code; char kind, name[18]; };
struct x910_probe_watch { const char *name; unsigned accepted, refused, exits; int pid; };
static struct x910_probe_watch x910_probe_watches[] = {
 {"launchd",0,0,0,0},{"watchdogd",0,0,0,0},{"backboardd",0,0,0,0},
 {"SpringBoard",0,0,0,0},{"runningboardd",0,0,0,0},{"logd",0,0,0,0}
};
static struct x910_probe_event x910_probe_events[12];
static unsigned x910_probe_started, x910_probe_enabled, x910_probe_lock,
 x910_probe_count, x910_probe_execs, x910_probe_refused, x910_probe_exits, x910_probe_dropped;
static unsigned x910_probe_seconds;
static const volatile int *x910_probe_data_stage, *x910_probe_data_status, *x910_probe_readonly;
static int x910_probe_policy;
static uint64_t x910_probe_deadline;
static void x910_probe_record(int pid,const char *name,char kind,int error,unsigned ns,uint64_t code) {
 if(!__atomic_load_n(&x910_probe_enabled,__ATOMIC_ACQUIRE))return;
 if(__atomic_exchange_n(&x910_probe_lock,1,__ATOMIC_ACQUIRE)) {
  __atomic_fetch_add(&x910_probe_dropped,1,__ATOMIC_RELAXED);return;
 }
 if(kind=='X')++x910_probe_exits;
 else {++x910_probe_execs;if(error)++x910_probe_refused;}
 struct x910_probe_event *e=&x910_probe_events[x910_probe_count++%12];
 e->pid=pid;e->error=error;e->ns=ns;e->code=code;e->kind=kind;
 strlcpy(e->name,name?name:"?",sizeof(e->name));
 for(unsigned i=0;i<sizeof(x910_probe_watches)/sizeof(x910_probe_watches[0]);++i) {
  struct x910_probe_watch *w=&x910_probe_watches[i];
  if(strcmp(e->name,w->name))continue;
  w->pid=pid;
  if(kind=='X')++w->exits;else if(error)++w->refused;else ++w->accepted;
 }
 __atomic_store_n(&x910_probe_lock,0,__ATOMIC_RELEASE);
}
void x910_userland_probe_exec(int pid,const char *name,unsigned kind,int error) {
 x910_probe_record(pid,name,kind==1?'S':'M',error,0,0);
}
void x910_userland_probe_exit(int pid,const char *name,int rv,unsigned ns,uint64_t code) {
 x910_probe_record(pid,name,'X',rv,ns,code);
}
void x910_userland_probe_policy(int status) {
 if(__atomic_load_n(&x910_probe_enabled,__ATOMIC_ACQUIRE))__atomic_store_n(&x910_probe_policy,status,__ATOMIC_RELEASE);
}
static void x910_probe_thread(void *argument,wait_result_t result) {
 (void)argument;(void)result;
 while(mach_absolute_time()<x910_probe_deadline) {
  assert_wait_deadline((event_t)&x910_probe_started,THREAD_UNINT,x910_probe_deadline);
  thread_block(THREAD_CONTINUE_NULL);
 }
 static char report[1800];
 int used=snprintf(report,sizeof(report),
  "X910 USERLAND REPORT after %u s (intentional checkpoint)\n"
  "Data stage=%d status=%d readonly=%d policy=%d free_pages=%x\n",
  x910_probe_seconds,*x910_probe_data_stage,*x910_probe_data_status,*x910_probe_readonly,
  __atomic_load_n(&x910_probe_policy,__ATOMIC_ACQUIRE),(unsigned)vm_page_free_count);
 if(__atomic_exchange_n(&x910_probe_lock,1,__ATOMIC_ACQUIRE)) {
  panic("%sprocess snapshot busy; dropped=%u",report,x910_probe_dropped);
 }
 unsigned count=x910_probe_count,execs=x910_probe_execs,refused=x910_probe_refused,exits=x910_probe_exits;
 struct x910_probe_event events[12];memcpy(events,x910_probe_events,sizeof(events));
 struct x910_probe_watch watches[6];memcpy(watches,x910_probe_watches,sizeof(watches));
 __atomic_store_n(&x910_probe_lock,0,__ATOMIC_RELEASE);
 used+=snprintf(report+used,sizeof(report)-used,"signature/MAC checks=%u refused=%u exits=%u dropped=%u\n",execs,refused,exits,x910_probe_dropped);
 for(unsigned i=0;i<6 && used<(int)sizeof(report)-140;++i)
  used+=snprintf(report+used,sizeof(report)-used,"%s sig_ok=%u refused=%u exits=%u pid=%d\n",
   watches[i].name,watches[i].accepted,watches[i].refused,watches[i].exits,watches[i].pid);
 unsigned n=count<12?count:12;
 for(unsigned i=0;i<n && used<(int)sizeof(report)-140;++i) {
  struct x910_probe_event *e=&events[(count-n+i)%12];
  used+=snprintf(report+used,sizeof(report)-used,"%c %s[%d] value=%x ns=%u code=%llx\n",
   e->kind,e->name,e->pid,(unsigned)e->error,e->ns,(unsigned long long)e->code);
 }
 panic("%s",report);
}
void x910_userland_probe_start(unsigned seconds,const volatile int *stage,const volatile int *status,const volatile int *readonly) {
 if(seconds<5 || seconds>300 || !stage || !status || !readonly || __atomic_exchange_n(&x910_probe_started,1,__ATOMIC_ACQUIRE))return;
 x910_probe_seconds=seconds;x910_probe_data_stage=stage;x910_probe_data_status=status;
 x910_probe_readonly=readonly;x910_probe_policy=-2;
 uint64_t interval=0;clock_interval_to_absolutetime_interval(seconds,NSEC_PER_SEC,&interval);
 x910_probe_deadline=mach_absolute_time()+interval;
 __atomic_store_n(&x910_probe_enabled,1,__ATOMIC_RELEASE);
 thread_t thread;
 kern_return_t error=kernel_thread_start(x910_probe_thread,NULL,&thread);
 if(error!=KERN_SUCCESS)panic("X910 USERLAND REPORT: thread start failed %d",error);
 thread_deallocate(thread);
}
