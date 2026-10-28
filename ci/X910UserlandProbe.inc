// SPDX-License-Identifier: MIT
// Opt-in observation only: never claims a signature check is a running UI.
#include <kern/clock.h>
#include <kern/thread.h>
#include <kern/sched_prim.h>
#include <vm/vm_page.h>
#include <vm/vm_map.h>
#include <kern/kcdata.h>
#include <kern/block_hint.h>
#include <kern/turnstile.h>
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
// Live user stacks of a UI process. Takes thread references under the task lock, then reads only that
// task's own frame records through vm_map_read_user; never writes the task or changes its threads.
extern void *x910_proc_task_ref(int,task_t *);
extern void x910_proc_task_release(void *);
static int x910_probe_stacks(const char *label,int pid,char *out,int used,int capacity) {
 task_t task=TASK_NULL;
 void *proc=pid>0?x910_proc_task_ref(pid,&task):NULL;
 if(used>=capacity-200){if(proc)x910_proc_task_release(proc);return used;}
 if(!proc || task==TASK_NULL) {
  if(proc)x910_proc_task_release(proc);
  return used+snprintf(out+used,capacity-used,"\n%s[%d] unavailable",label,pid);
 }
 thread_t threads[3];unsigned n=0,total=0;thread_t thread;
 task_lock(task);
 queue_iterate(&task->threads,thread,thread_t,task_threads) {
  ++total;
  if(n<3){thread_reference(thread);threads[n++]=thread;}
 }
 task_unlock(task);
 used+=snprintf(out+used,capacity-used,"\n%s[%d] threads=%u cache+=%x",label,pid,total,task->task_shared_region_slide);
 for(unsigned i=0;i<n;++i) {
  thread_t t=threads[i];arm_saved_state_t *s=get_user_regs(t);
  uint64_t pc=s?get_saved_state_pc(s):0,lr=s?get_saved_state_lr(s):0,fp=s?get_saved_state_fp(s):0;
  if(used<capacity-200)
   used+=snprintf(out+used,capacity-used,"\n T%u st=%x w=%llx pc=%llx lr=%llx:",i,(unsigned)t->state,
    (unsigned long long)t->wait_event,(unsigned long long)pc,(unsigned long long)lr);
  for(unsigned d=0;d<6 && fp && !(fp&7) && used<capacity-40;++d) {
   uint64_t pair[2];
   if(vm_map_read_user(task->map,fp,pair,sizeof(pair))!=KERN_SUCCESS)break;
   used+=snprintf(out+used,capacity-used," %llx",(unsigned long long)pair[1]);
   if(pair[0]<=fp)break;
   fp=pair[0];
  }
  // Who this thread waits for, as stackshot reports it: block hint, receiver of the waited port,
  // final turnstile inheritor (pid if blocked on a task) and the launchd service name of the port.
  // Read with interrupts off on the single CPU, so the blocked thread's wait state is stable. The
  // turnstile walkers are stackshot code and insist on debugger context (they panic otherwise and
  // skip locks inside it), so not_in_kdp is cleared for exactly these reads and restored at once.
  thread_waitinfo_v2_t wi={0};thread_turnstileinfo_v2_t ti={0};
  struct ipc_service_port_label *ispl=NULL;char service[56]={0};
  boolean_t istate=ml_set_interrupts_enabled(FALSE);
  unsigned int kdp_state=not_in_kdp;not_in_kdp=0;
  wi.wait_type=t->block_hint;
  if(t->block_hint==kThreadWaitPortReceive && t->wait_event==NO_EVENT64 /* IPC_MQUEUE_RECEIVE */ && !waitq_is_null(t->waitq))
   kdp_mqueue_recv_find_owner(t->waitq.wq_q,t->wait_event,&wi,&ispl);
  struct turnstile *ts=thread_get_waiting_turnstile(t);
  if(ts!=TURNSTILE_NULL) {
   struct ipc_service_port_label *chain=NULL;
   kdp_turnstile_fill_tsinfo(ts,&ti,&chain);
   if(chain)ispl=chain;
  }
  if(ispl) {
   struct portlabel_info label={0};const char *name=NULL;
   kdp_ipc_fill_splabel(ispl,&label,&name);
   if(name)strlcpy(service,name,sizeof(service));
  }
  not_in_kdp=kdp_state;
  ml_set_interrupts_enabled(istate);
  // An idle run loop waits on a port set; only the other waits are printed.
  if(used<capacity-120 && wi.wait_type!=kThreadWaitPortSetReceive && (t->block_hint || ts!=TURNSTILE_NULL))
   used+=snprintf(out+used,capacity-used,"\n  hint=%u rcv=%llx%s ts=%llx/%llx/%u %s",(unsigned)t->block_hint,
    (unsigned long long)wi.owner,(wi.wait_flags&STACKSHOT_WAITINFO_FLAGS_SPECIALREPLY)?"r":"",
    (unsigned long long)ti.turnstile_context,(unsigned long long)ti.turnstile_flags,(unsigned)ti.number_of_hops,service);
  thread_deallocate(t);
 }
 x910_proc_task_release(proc);
 return used;
}
static void x910_probe_thread(void *argument,wait_result_t result) {
 (void)argument;(void)result;
 while(mach_absolute_time()<x910_probe_deadline) {
  assert_wait_deadline((event_t)&x910_probe_started,THREAD_UNINT,x910_probe_deadline);
  thread_block(THREAD_CONTINUE_NULL);
 }
 static char report[6144];
 int used=snprintf(report,sizeof(report),
  "X910 USERLAND REPORT after %u s (intentional checkpoint)\n"
  "Data stage=%d status=%d readonly=%d policy=%d free_pages=%x\n",
  x910_probe_seconds,*x910_probe_data_stage,*x910_probe_data_status,*x910_probe_readonly,
  __atomic_load_n(&x910_probe_policy,__ATOMIC_ACQUIRE),(unsigned)vm_page_free_count);
 if(__atomic_exchange_n(&x910_probe_lock,1,__ATOMIC_ACQUIRE)) {
  panic("%sprocess snapshot busy; dropped=%u",report,x910_probe_dropped);
 }
 unsigned execs=x910_probe_execs,refused=x910_probe_refused,exits=x910_probe_exits;
 struct x910_probe_watch watches[6];memcpy(watches,x910_probe_watches,sizeof(watches));
 __atomic_store_n(&x910_probe_lock,0,__ATOMIC_RELEASE);
 used+=snprintf(report+used,sizeof(report)-used,"signature/MAC checks=%u refused=%u exits=%u dropped=%u",execs,refused,exits,x910_probe_dropped);
 // Live display and presentation state, before the process lists so it is never truncated.
 extern void x910_ui_display_snapshot(char *,unsigned long);
 x910_ui_display_snapshot(report,sizeof(report));
 extern void x910_iomfb_calls_report(char *,unsigned long);
 x910_iomfb_calls_report(report,sizeof(report));
 used=(int)strlen(report);
 // Where the UI processes are while alive: their threads' user PC/LR and frame chain.
 for(unsigned i=0;i<6;++i)
  if(!strcmp(watches[i].name,"SpringBoard") || !strcmp(watches[i].name,"backboardd"))
   used=x910_probe_stacks(watches[i].name,watches[i].pid,report,used,(int)sizeof(report));
 used+=snprintf(report+used,sizeof(report)-used,"\nsig_ok/refused/exits/pid:");
 for(unsigned i=0;i<6 && used<(int)sizeof(report)-140;++i)
  used+=snprintf(report+used,sizeof(report)-used,"%s%s %u/%u/%u/%d",i%3?"  ":"\n",
   watches[i].name,watches[i].accepted,watches[i].refused,watches[i].exits,watches[i].pid);
 used+=snprintf(report+used,sizeof(report)-used,"\n");
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
