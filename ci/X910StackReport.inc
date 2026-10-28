// SPDX-License-Identifier: MIT
// Called only by the original invalid-stack overflow panic path. No allocation.
#include <machine/machine_routines.h>
const char *x910_stack_overflow_report(void *saved,uint64_t bottom,uint64_t top) {
 static unsigned busy;
 static char message[900];
 if(__atomic_exchange_n(&busy,1,__ATOMIC_ACQUIRE))return "Invalid kernel stack pointer (probable overflow); X910 capture busy";
 arm_saved_state_t *state=saved;
 uint64_t frame=get_saved_state_fp(state);
 unsigned used=(unsigned)snprintf(message,sizeof(message),
  "Invalid kernel stack pointer (probable overflow).\nX910 stack=%llx..%llx FP=%llx returns:",bottom,top,frame);
 for(unsigned i=0;i<12 && used<sizeof(message)-70;++i) {
  uint64_t pair[2];
  if((frame&15) || frame<bottom || frame>=top || top-frame<sizeof(pair))break;
  if(ml_nofault_copy((vm_offset_t)frame,(vm_offset_t)pair,sizeof(pair))!=sizeof(pair))break;
  used+=(unsigned)snprintf(message+used,sizeof(message)-used,"%s%llx",i%4==0?"\n":" ",pair[1]);
  if(pair[0]<=frame || pair[0]>=top)break;
  frame=pair[0];
 }
 return message;
}
