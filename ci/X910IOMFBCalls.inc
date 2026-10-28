// SPDX-License-Identifier: MIT
// Observation only: IOMobileFramebufferUserClient external method calls, counted by selector with
// the last result, plus the most recent calls with their caller. Never changes a call or its result.
#define X910_IOMFB_SELECTORS 128
#define X910_IOMFB_RECENT 16
struct x910_iomfb_call { int pid; uint32_t selector; IOReturn ret; uint32_t async; };
static volatile uint32_t x910_iomfb_counts[X910_IOMFB_SELECTORS],x910_iomfb_errors[X910_IOMFB_SELECTORS];
static volatile IOReturn x910_iomfb_last_ret[X910_IOMFB_SELECTORS];
static struct x910_iomfb_call x910_iomfb_recent[X910_IOMFB_RECENT];
static volatile uint32_t x910_iomfb_total,x910_iomfb_other;
static void
x910_iomfb_call_observed(IOUserClient *client, uint32_t selector, IOExternalMethodArguments *args, IOReturn ret)
{
	const OSMetaClass *meta = client->getMetaClass();
	if (!meta || strcmp(meta->getClassName(), "IOMobileFramebufferUserClient")) {
		return;
	}
	uint32_t n = __atomic_fetch_add(&x910_iomfb_total, 1, __ATOMIC_RELAXED);
	if (selector < X910_IOMFB_SELECTORS) {
		x910_iomfb_counts[selector] = x910_iomfb_counts[selector] + 1;
		x910_iomfb_last_ret[selector] = ret;
		if (ret != kIOReturnSuccess) {
			x910_iomfb_errors[selector] = x910_iomfb_errors[selector] + 1;
		}
	} else {
		x910_iomfb_other = x910_iomfb_other + 1;
	}
	struct x910_iomfb_call *e = &x910_iomfb_recent[n % X910_IOMFB_RECENT];
	e->pid = proc_selfpid(); e->selector = selector; e->ret = ret;
	e->async = args && args->asyncWakePort != MACH_PORT_NULL;
}
// Appends "sel:count/errors/lastret" for every selector seen, then the recent calls oldest first.
extern "C" void x910_iomfb_calls_report(char *out, unsigned long capacity);
extern "C" void
x910_iomfb_calls_report(char *out, unsigned long capacity)
{
	unsigned long used = strlen(out);
	uint32_t total = x910_iomfb_total;
	if (used >= capacity) {
		return;
	}
	used += snprintf(out + used, capacity - used, "\nIOMFB calls=%u other=%u sel:n/err/ret", total, x910_iomfb_other);
	unsigned shown = 0;
	for (unsigned s = 0; s < X910_IOMFB_SELECTORS && used < capacity; ++s) {
		if (!x910_iomfb_counts[s]) {
			continue;
		}
		used += snprintf(out + used, capacity - used, "%s%u:%u/%u/%x", shown % 8 ? " " : "\n ", s,
		    x910_iomfb_counts[s], x910_iomfb_errors[s], (unsigned)x910_iomfb_last_ret[s]);
		++shown;
	}
	unsigned count = total < X910_IOMFB_RECENT ? total : X910_IOMFB_RECENT;
	if (used < capacity) {
		used += snprintf(out + used, capacity - used, "\nIOMFB last pid:sel:ret");
	}
	for (unsigned i = 0; i < count && used < capacity; ++i) {
		const struct x910_iomfb_call *e = &x910_iomfb_recent[(total - count + i) % X910_IOMFB_RECENT];
		used += snprintf(out + used, capacity - used, "%s%d:%u:%x%s", i % 8 ? " " : "\n ", e->pid, e->selector,
		    (unsigned)e->ret, e->async ? "a" : "");
	}
}
