"""X910 experiment: advertise its DMA aperture and gate nonstandard host capability.

Run BEFORE Ath12kWifi/tools/prepare.sh; this appends a checked generated-source
transform. Also patch a COPY of WifiProbe.cpp supplied as the second argument.
No firmware files, hardware, or active worktree are touched implicitly.
"""
from pathlib import Path
import sys

POST = r'''
# X910_HOSTCAP_RANGE_V1: generated qmi.c is a private copy, never edit Linux deps.
python3 - <<'X910_HOSTCAP_PY'
from pathlib import Path
p=Path('out/ath12k-src/qmi.c')
s=p.read_text()
def once(old,new):
 global s
 if s.count(old)!=1:raise SystemExit('X910 host-cap context changed: '+old[:100])
 s=s.replace(old,new)
mark='static const struct qmi_elem_info qmi_wlanfw_host_cap_req_msg_v01_ei[] = {'
descriptor=''' + repr(r'''
extern int x910_wifi_dma_range(u64 *, u64 *);
static struct qmi_elem_info x910_host_ddr_range_ei[] = {
 { .data_type=QMI_UNSIGNED_8_BYTE, .elem_len=1, .elem_size=sizeof(u64),
   .array_type=NO_ARRAY, .tlv_type=0, .offset=offsetof(struct qmi_wlanfw_host_ddr_range,start) },
 { .data_type=QMI_UNSIGNED_8_BYTE, .elem_len=1, .elem_size=sizeof(u64),
   .array_type=NO_ARRAY, .tlv_type=0, .offset=offsetof(struct qmi_wlanfw_host_ddr_range,size) },
 { .data_type=QMI_EOTI, .array_type=NO_ARRAY, .tlv_type=QMI_COMMON_TLV_TYPE }
};
''') + r'''
once(mark,descriptor+mark)
old='\t\t\t\t\t   ddr_range),\n\t},\n'
once(old,old.replace('\n\t},','\n\t\t.ei_array\t= x910_host_ddr_range_ei,\n\t},'))
old='\treq.nw_on_demand_mem_valid = 1;\n\treq.nw_on_demand_mem = 1;\n'
new=''' + repr(r'''
 /* CNSS on Samsung advertises the S1 IOVA aperture in TLV 0x1f.
  * Never invent physical addresses or copy the image into the remote heap. */
 u64 x910_start=0,x910_size=0;
 if (!x910_wifi_dma_range(&x910_start,&x910_size) || !x910_size ||
     x910_start>=0x100000000ULL || x910_size>0x100000000ULL-x910_start)
  return -EINVAL;
 req.ddr_range_valid=1;
 req.ddr_range[0].start=x910_start;
 req.ddr_range[0].size=x910_size;
 /* The stock Samsung schema stops at 0x31; only send the additional 0x33
  * capability if the device actually reports support for it. */
 req.nw_on_demand_mem_valid=nw_on_demand_ddr!=0;
 req.nw_on_demand_mem=nw_on_demand_ddr!=0;
 ath12k_dbg(ab,ATH12K_DBG_QMI,
  "x910 hostcap: ddr aperture %llx+%llx on-demand valid=%u value=%u\n",
  x910_start,x910_size,req.nw_on_demand_mem_valid,req.nw_on_demand_mem);
''').replace('value=%u\n','value=%u\\n') + r'''
once(old,new)
p.write_text(s)
print('X910 hostcap: range descriptor/aperture and negotiated on-demand capability')
X910_HOSTCAP_PY
'''

BOARD_OLD = 'int pMsiTarget(uint32_t *lo, uint32_t *hi, uint32_t *data)'
BOARD_NEW = '''extern "C" int x910_wifi_dma_range(uint64_t *start,uint64_t *bytes) {
    if(!start || !bytes || !gArena.va || !gArena.bytes)return 0;
    *start=gArena.iova;*bytes=gArena.bytes;return 1;
}
''' + BOARD_OLD

def main():
    module=Path(sys.argv[1]);board=Path(sys.argv[2]);prep=module/'tools/prepare.sh'
    s=prep.read_text();b=board.read_text()
    if 'X910_HOSTCAP_RANGE_V1' in s or 'x910_wifi_dma_range' in b:
        raise SystemExit('Refusing an already transformed input')
    if s.count("req.nw_on_demand_mem_valid = 1;")!=1 or b.count(BOARD_OLD)!=1:
        raise SystemExit('Pinned module/board context changed')
    # Parse the embedded Python before changing either input.
    body=POST.split("python3 - <<'X910_HOSTCAP_PY'\n",1)[1].rsplit('\nX910_HOSTCAP_PY',1)[0]
    compile(body,'x910-hostcap-generated','exec')
    prep.write_bytes((s+POST).encode())
    board.write_bytes(b.replace(BOARD_OLD,BOARD_NEW).encode())
    print('X910 hostcap transform prepared; generated qmi context checks run after prepare.sh')

if __name__=='__main__':main()
