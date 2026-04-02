/*

Copyright 2019-2024, The Regents of the University of California.
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

   1. Redistributions of source code must retain the above copyright notice,
      this list of conditions and the following disclaimer.

   2. Redistributions in binary form must reproduce the above copyright notice,
      this list of conditions and the following disclaimer in the documentation
      and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE REGENTS OF THE UNIVERSITY OF CALIFORNIA ''AS
IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE REGENTS OF THE UNIVERSITY OF CALIFORNIA OR
CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT
OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING
IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY
OF SUCH DAMAGE.

The views and conclusions contained in the software and documentation are those
of the authors and should not be interpreted as representing official policies,
either expressed or implied, of The Regents of the University of California.

*/

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include "timespec.h"

#include <mqnic/mqnic.h>

#define NSEC_PER_SEC 1000000000

static void usage(char *name)
{
    fprintf(stderr,
        "usage: %s [options]\n"
        " -d name    device to open (/dev/mqnic0)\n"
        " -s number  TDMA schedule start time (ns)\n"
        " -p number  TDMA schedule period (ns)\n"
        " -t number  TDMA timeslot period (ns)\n"
        " -a number  TDMA active period (ns)\n"
        " -m number  Channel mask (default all 1s)\n"
        " -g number  PRBS31 generation\n"
        " -i number  TDMA measurement interval (s)\n"
        " -c file    write heat map CSV\n"
        " -k number  heat map slice count (default 128)\n",
        name);
}

int main(int argc, char *argv[])
{
    char *name;
    int opt;

    char *device = NULL;
    struct mqnic *dev;

    struct timespec ts_now;
    struct timespec ts_start;
    struct timespec ts_period;

    int64_t start_nsec = 0;
    uint32_t period_nsec = 0;
    uint32_t timeslot_period_nsec = 0;
    uint32_t active_period_nsec = 0;

    int channel_mask = 0xffffffff;
    int prbs_control = -1;
    float interval = -1;

    char *csv_file_name = NULL;
    FILE *csv_file = NULL;

    int slice_count = 128;

    name = strrchr(argv[0], '/');
    name = name ? 1+name : argv[0];

    while ((opt = getopt(argc, argv, "d:s:p:t:a:m:g:i:c:k:h?")) != EOF)
    {
        switch (opt)
        {
        case 'd':
            device = optarg;
            break;
        case 's':
            start_nsec = atoll(optarg);
            break;
        case 'p':
            period_nsec = atoi(optarg);
            break;
        case 't':
            timeslot_period_nsec = atoi(optarg);
            break;
        case 'a':
            active_period_nsec = atoi(optarg);
            break;
        case 'm':
            channel_mask = strtol(optarg, 0, 0);
            break;
        case 'g':
            prbs_control = atoi(optarg);
            break;
        case 'i':
            interval = atof(optarg);
            break;
        case 'c':
            csv_file_name = optarg;
            break;
        case 'k':
            slice_count = atoi(optarg);
            break;
        case 'h':
        case '?':
            usage(name);
            return 0;
        default:
            usage(name);
            return -1;
        }
    }

    if (!device)
    {
        fprintf(stderr, "Device not specified\n");
        usage(name);
        return -1;
    }

    dev = mqnic_open(device);

    if (!dev)
    {
        fprintf(stderr, "Failed to open device\n");
        return -1;
    }

    if (dev->pci_device_path[0])
    {
        char *ptr = strrchr(dev->pci_device_path, '/');
        if (ptr)
            printf("PCIe ID: %s\n", ptr+1);
    }

    mqnic_print_fw_id(dev);

    if (!dev->phc_rb)
    {
        fprintf(stderr, "No PHC on card\n");
        goto err;
    }

    struct mqnic_reg_block *tdma_ber_block_rb = mqnic_find_reg_block(dev->rb_list, 0x0000c061, 0x00000100, 0);

    if (!tdma_ber_block_rb)
    {
        fprintf(stderr, "TDMA BER block not found\n");
        goto err;
    }

    struct mqnic_reg_block *tdma_ber_rb_list = mqnic_enumerate_reg_block_list(dev->regs, mqnic_reg_read32(tdma_ber_block_rb->regs, 0xC), dev->regs_size);

    printf("TDMA BER register blocks:\n");
    for (struct mqnic_reg_block *rb = tdma_ber_rb_list; rb->regs; rb++)
        printf(" type 0x%08x (v %d.%d.%d.%d)\n", rb->type, rb->version >> 24,
                (rb->version >> 16) & 0xff, (rb->version >> 8) & 0xff, rb->version & 0xff);

    struct mqnic_reg_block *tdma_sched_rb = mqnic_find_reg_block(tdma_ber_rb_list, MQNIC_RB_TDMA_SCH_TYPE, MQNIC_RB_TDMA_SCH_VER, 0);
    struct mqnic_reg_block *tdma_ber_rb = mqnic_find_reg_block(tdma_ber_rb_list, 0x0000c062, 0x00000100, 0);

    if (!tdma_sched_rb || !tdma_ber_rb)
    {
        fprintf(stderr, "Required block not found\n");
        goto err;
    }

    uint32_t timeslot_count = mqnic_reg_read32(tdma_sched_rb->regs, MQNIC_RB_TDMA_SCH_REG_CTRL) >> 16;
    uint32_t channel_count = (mqnic_reg_read32(tdma_ber_rb->regs, 0x0C) >> 8) & 0xff;
    uint32_t bits_per_update = mqnic_reg_read32(tdma_ber_rb->regs, 0x0C) >> 16;

    mqnic_reg_write32(tdma_ber_rb->regs, 0x18, 0x7fffffff);
    uint32_t ram_size = mqnic_reg_read32(tdma_ber_rb->regs, 0x18)+1;

    printf("TDMA control: 0x%08x\n", mqnic_reg_read32(tdma_sched_rb->regs, MQNIC_RB_TDMA_SCH_REG_CTRL));
    printf("TDMA timeslot count: %d\n", timeslot_count);

    printf("TDMA schedule start:  %ld.%09d s\n", mqnic_reg_read32(tdma_sched_rb->regs, MQNIC_RB_TDMA_SCH_REG_SCH_START_SEC_L) +
            (((int64_t)mqnic_reg_read32(tdma_sched_rb->regs, MQNIC_RB_TDMA_SCH_REG_SCH_START_SEC_H)) << 32),
            mqnic_reg_read32(tdma_sched_rb->regs, MQNIC_RB_TDMA_SCH_REG_SCH_START_NS));
    printf("TDMA schedule period: %d ns\n", mqnic_reg_read32(tdma_sched_rb->regs, MQNIC_RB_TDMA_SCH_REG_SCH_PERIOD_NS));
    printf("TDMA timeslot period: %d ns\n", mqnic_reg_read32(tdma_sched_rb->regs, MQNIC_RB_TDMA_SCH_REG_TS_PERIOD_NS));
    printf("TDMA active period:   %d ns\n", mqnic_reg_read32(tdma_sched_rb->regs, MQNIC_RB_TDMA_SCH_REG_ACTIVE_PERIOD_NS));

    printf("TDMA BER control: 0x%08x\n", mqnic_reg_read32(tdma_ber_rb->regs, 0x0C));
    printf("TDMA BER channel count: %d\n", channel_count);
    printf("TDMA BER bits per update: %d\n", bits_per_update);
    printf("TDMA BER TX PRBS31 enable: 0x%08x\n", mqnic_reg_read32(tdma_ber_rb->regs, 0x10));
    printf("TDMA BER RX PRBS31 enable: 0x%08x\n", mqnic_reg_read32(tdma_ber_rb->regs, 0x14));
    printf("TDMA BER RAM size: %d\n", ram_size);
    printf("TDMA BER slice time: %d ns\n", mqnic_reg_read32(tdma_ber_rb->regs, 0x20));
    printf("TDMA BER slice offset: %d ns\n", mqnic_reg_read32(tdma_ber_rb->regs, 0x24));
    printf("TDMA BER slice shift: %d\n", mqnic_reg_read32(tdma_ber_rb->regs, 0x28));

    float rate[32];

    // enable PRBS RX
    mqnic_reg_write32(tdma_ber_rb->regs, 0x14, channel_mask);

    for (int i = 0; i < channel_count; i++)
    {
        uint32_t ns = mqnic_reg_read32(dev->phc_rb->regs, MQNIC_RB_PHC_REG_CUR_REL_NS_L);
        uint32_t b = mqnic_reg_read32(tdma_ber_rb->regs, 0x40 + i*16);

        usleep(10000);

        ns = (mqnic_reg_read32(dev->phc_rb->regs, MQNIC_RB_PHC_REG_CUR_REL_NS_L) - ns) & 0xffffffff;
        b = (mqnic_reg_read32(tdma_ber_rb->regs, 0x40 + i*16) - b) & 0xffffffff;

        rate[i] = (float)b * bits_per_update / ns;
        printf("TDMA BER CH%d rate: %f Gbps\n", i, rate[i]);
    }

    // disable PRBS RX
    mqnic_reg_write32(tdma_ber_rb->regs, 0x14, 0);

    if (period_nsec > 0)
    {
        printf("Configure BER TDMA schedule\n");

        ts_now.tv_nsec = mqnic_reg_read32(dev->phc_rb->regs, MQNIC_RB_PHC_REG_CUR_TOD_NS);
        ts_now.tv_sec = mqnic_reg_read32(dev->phc_rb->regs, MQNIC_RB_PHC_REG_CUR_TOD_SEC_L) + (((int64_t)mqnic_reg_read32(dev->phc_rb->regs, MQNIC_RB_PHC_REG_CUR_TOD_SEC_H)) << 32);

        // normalize start
        ts_start.tv_sec = start_nsec / NSEC_PER_SEC;
        ts_start.tv_nsec = start_nsec - ts_start.tv_sec * NSEC_PER_SEC;

        // normalize period
        ts_period.tv_sec = period_nsec / NSEC_PER_SEC;
        ts_period.tv_nsec = period_nsec - ts_period.tv_sec * NSEC_PER_SEC;

        printf("time   %ld.%09ld s\n", ts_now.tv_sec, ts_now.tv_nsec);
        printf("start  %ld.%09ld s\n", ts_start.tv_sec, ts_start.tv_nsec);
        printf("period %d ns\n", period_nsec);

        if (timespec_lt(ts_start, ts_now))
        {
            // start time is in the past

            // modulo start with period
            ts_start = timespec_mod(ts_start, ts_period);

            // align time with period
            struct timespec ts_aligned = timespec_sub(ts_now, timespec_mod(ts_now, ts_period));

            // add aligned time
            ts_start = timespec_add(ts_start, ts_aligned);
        }

        printf("time   %ld.%09ld s\n", ts_now.tv_sec, ts_now.tv_nsec);
        printf("start  %ld.%09ld s\n", ts_start.tv_sec, ts_start.tv_nsec);
        printf("period %d ns\n", period_nsec);

        mqnic_reg_write32(tdma_sched_rb->regs, MQNIC_RB_TDMA_SCH_REG_SCH_START_NS, ts_start.tv_nsec);
        mqnic_reg_write32(tdma_sched_rb->regs, MQNIC_RB_TDMA_SCH_REG_SCH_START_SEC_L, ts_start.tv_sec & 0xffffffff);
        mqnic_reg_write32(tdma_sched_rb->regs, MQNIC_RB_TDMA_SCH_REG_SCH_START_SEC_H, ts_start.tv_sec >> 32);
        mqnic_reg_write32(tdma_sched_rb->regs, MQNIC_RB_TDMA_SCH_REG_SCH_PERIOD_NS, period_nsec);

        mqnic_reg_write32(tdma_sched_rb->regs, MQNIC_RB_TDMA_SCH_REG_CTRL, 0x00000001);
    }

    if (timeslot_period_nsec > 0)
    {
        printf("Configure port TDMA timeslot period\n");

        printf("period %d ns\n", timeslot_period_nsec);

        mqnic_reg_write32(tdma_sched_rb->regs, MQNIC_RB_TDMA_SCH_REG_TS_PERIOD_NS, timeslot_period_nsec);
    }

    if (active_period_nsec > 0)
    {
        printf("Configure port TDMA active period\n");

        printf("period %d ns\n", active_period_nsec);

        mqnic_reg_write32(tdma_sched_rb->regs, MQNIC_RB_TDMA_SCH_REG_ACTIVE_PERIOD_NS, active_period_nsec);
    }

    // read current schedule parameters
    ts_start.tv_nsec = mqnic_reg_read32(tdma_sched_rb->regs, MQNIC_RB_TDMA_SCH_REG_SCH_START_NS);
    ts_start.tv_sec = mqnic_reg_read32(tdma_sched_rb->regs, MQNIC_RB_TDMA_SCH_REG_SCH_START_SEC_L) +
        ((int64_t)mqnic_reg_read32(tdma_sched_rb->regs, MQNIC_RB_TDMA_SCH_REG_SCH_START_SEC_H) << 32);

    period_nsec = mqnic_reg_read32(tdma_sched_rb->regs, MQNIC_RB_TDMA_SCH_REG_SCH_PERIOD_NS);
    timeslot_period_nsec = mqnic_reg_read32(tdma_sched_rb->regs, MQNIC_RB_TDMA_SCH_REG_TS_PERIOD_NS);
    active_period_nsec = mqnic_reg_read32(tdma_sched_rb->regs, MQNIC_RB_TDMA_SCH_REG_ACTIVE_PERIOD_NS);

    if (active_period_nsec > timeslot_period_nsec)
        active_period_nsec = timeslot_period_nsec;

    uint32_t slot_count = timeslot_period_nsec ? (period_nsec+timeslot_period_nsec-1) / timeslot_period_nsec : 1;

    if (prbs_control >= 0)
    {
        printf("Configure PRBS generation\n");

        uint32_t tx_val = mqnic_reg_read32(tdma_ber_rb->regs, 0x10);

        if (prbs_control)
        {
            tx_val |= channel_mask;
        }
        else
        {
            tx_val &= ~channel_mask;
        }

        mqnic_reg_write32(tdma_ber_rb->regs, 0x10, tx_val);
    }

    if (slot_count > timeslot_count)
    {
        fprintf(stderr, "Error: schedule defines more timeslots than the TDMA scheduler supports (%d > %d)\n", slot_count, timeslot_count);
        goto err;
    }

    if (csv_file_name)
    {
        time_t cur_time;
        struct tm *tm_info;
        char datestr[32];

        // time string
        time(&cur_time);
        tm_info = localtime(&cur_time);
        strftime(datestr, sizeof(datestr), "%F %T", tm_info);

        if (slice_count <= 0)
        {
            fprintf(stderr, "Invalid slice count\n");
            goto err;
        }

        printf("Measuring heat map to %s\n", csv_file_name);

        csv_file = fopen(csv_file_name, "w");

        fprintf(csv_file, "#TDMA BER\n");
        fprintf(csv_file, "#date,'%s'\n", datestr);

        if (dev->pci_device_path[0])
        {
            char *ptr = strrchr(dev->pci_device_path, '/');
            if (ptr)
                fprintf(csv_file, "#pcie_id,%s\n", ptr+1);
        }

        fprintf(csv_file, "#fpga_id,0x%08x\n", dev->fpga_id);
        fprintf(csv_file, "#fw_id,0x%08x\n", dev->fw_id);
        fprintf(csv_file, "#fw_version,'%d.%d.%d.%d'\n", dev->fw_ver >> 24,
                (dev->fw_ver >> 16) & 0xff,
                (dev->fw_ver >> 8) & 0xff,
                dev->fw_ver & 0xff);
        fprintf(csv_file, "#board_id,0x%08x\n", dev->board_id);
        fprintf(csv_file, "#board_version,'%d.%d.%d.%d'\n", dev->board_ver >> 24,
                (dev->board_ver >> 16) & 0xff,
                (dev->board_ver >> 8) & 0xff,
                dev->board_ver & 0xff);
        fprintf(csv_file, "#build_date,'%s UTC'\n", dev->build_date_str);
        fprintf(csv_file, "#git_hash,'%08x'\n", dev->git_hash);
        fprintf(csv_file, "#release_info,'%08x'\n", dev->rel_info);

        fprintf(csv_file, "#start,%ld.%09ld\n", ts_start.tv_sec, ts_start.tv_nsec);
        fprintf(csv_file, "#period_ns,%d\n", period_nsec);
        fprintf(csv_file, "#timeslot_period_ns,%d\n", timeslot_period_nsec);
        fprintf(csv_file, "#active_period_ns,%d\n", active_period_nsec);
        fprintf(csv_file, "#channel_count,%d\n", channel_count);
        fprintf(csv_file, "#channel_mask,0x%08x\n", channel_mask);

        for (int i = 0; i < channel_count; i++)
        {
            fprintf(csv_file, "#channel_%d_rate,%f\n", i, rate[i]);
        }

        uint32_t slice_num = 0;
        uint32_t slice_time = active_period_nsec / slice_count;
        uint32_t slice_batch = 0;
        uint32_t slice_offset = 0;
        uint32_t slice_shift = 0;

        for (slice_shift = 16; slice_shift > 0; slice_shift--)
        {
            slice_batch = 1 << slice_shift;
            if (slice_batch * slot_count <= ram_size)
                break;
        }

        fprintf(csv_file, "#slot_count,%d\n", slot_count);
        fprintf(csv_file, "#slice_count,%d\n", slice_count);
        fprintf(csv_file, "#slice_time_ns,%d\n", slice_time);
        fprintf(csv_file, "channel,slot,slice,offset_ns,slot_offset_ns,duration_ns,bits,errors\n");

        printf("slot count %d\n", slot_count);
        printf("slice count %d\n", slice_count);
        printf("slice batch %d\n", slice_batch);
        printf("slice shift %d\n", slice_shift);
        printf("start  %ld.%09ld s\n", ts_start.tv_sec, ts_start.tv_nsec);
        printf("period %d ns\n", period_nsec);
        printf("timeslot period %d ns\n", timeslot_period_nsec);
        printf("active period %d ns\n", active_period_nsec);

        // enable PRBS RX
        mqnic_reg_write32(tdma_ber_rb->regs, 0x14, channel_mask);

        // stop accumulation
        mqnic_reg_write32(tdma_ber_rb->regs, 0x0C, 0);

        while (slice_num < slice_count)
        {
            printf("slice %d / %d\n", slice_num, slice_count);
            printf("slice time %d ns\n", slice_time);
            printf("slice offset %d ns\n", slice_offset);

            // configure slice
            mqnic_reg_write32(tdma_ber_rb->regs, 0x20, slice_time);
            mqnic_reg_write32(tdma_ber_rb->regs, 0x24, slice_offset);
            mqnic_reg_write32(tdma_ber_rb->regs, 0x28, slice_shift);

            // zero counters
            for (int i = 0; i < slot_count*slice_batch; i++)
            {
                mqnic_reg_write32(tdma_ber_rb->regs, 0x18, i | 0x80000000);
            }

            sleep(1);

            // start accumulation in slice mode
            mqnic_reg_write32(tdma_ber_rb->regs, 0x0C, 3);

            sleep(interval);

            // stop accumulation
            mqnic_reg_write32(tdma_ber_rb->regs, 0x0C, 0);

            for (int j = 0; j < slot_count; j++)
            {
                for (int k = 0; k < slice_batch; k++)
                {
                    // select slice
                    mqnic_reg_write32(tdma_ber_rb->regs, 0x18, (j<<slice_shift)+k);
                    mqnic_reg_read32(tdma_ber_rb->regs, 0x18);

                    for (int i = 0; i < channel_count; i++)
                    {
                        if (channel_mask & (1 << i))
                        {
                            int64_t bits = ((int64_t)mqnic_reg_read32(tdma_ber_rb->regs, 0x48 + i*16)) * bits_per_update;
                            int64_t errs = mqnic_reg_read32(tdma_ber_rb->regs, 0x4C + i*16);
                            fprintf(csv_file, "%d,%d,%d,%d,%d,%d,%ld,%ld\n", i, j, k+slice_num, j*timeslot_period_nsec+slice_offset+k*slice_time, slice_offset+k*slice_time, slice_time, bits, errs);
                        }
                    }
                }
            }

            fflush(csv_file);

            slice_num += slice_batch;
            slice_offset += slice_time*slice_batch;
        }

        // disable PRBS RX
        mqnic_reg_write32(tdma_ber_rb->regs, 0x14, 0);

        fclose(csv_file);
    }
    else if (interval > 0)
    {
        printf("TDMA BER counters\n");

        // enable PRBS RX
        mqnic_reg_write32(tdma_ber_rb->regs, 0x14, channel_mask);

        // stop accumulation
        mqnic_reg_write32(tdma_ber_rb->regs, 0x0C, 0);

        // zero counters
        for (int i = 0; i < slot_count; i++)
        {
            mqnic_reg_write32(tdma_ber_rb->regs, 0x18, i | 0x80000000);
        }

        // start accumulation
        mqnic_reg_write32(tdma_ber_rb->regs, 0x0C, 1);

        sleep(interval);

        // stop accumulation
        mqnic_reg_write32(tdma_ber_rb->regs, 0x0C, 0);

        // disable PRBS RX
        mqnic_reg_write32(tdma_ber_rb->regs, 0x14, 0);

        printf("   ");
        for (int i = 0; i < channel_count; i++)
        {
            if (channel_mask & (1 << i))
            {
                printf("  ch %02d     ", i);
                printf("  ch %02d     ", i);
                printf("  ch %02d     ", i);
            }
        }

        printf("\n");

        printf("   ");
        for (int i = 0; i < channel_count; i++)
        {
            if (channel_mask & (1 << i))
            {
                printf("  bits      ");
                printf("  errors    ");
                printf("  BER       ");
            }
        }

        printf("\n");

        for (int j = 0; j < slot_count; j++)
        {
            printf("%02d   ", j);
            mqnic_reg_write32(tdma_ber_rb->regs, 0x18, j);
            mqnic_reg_read32(tdma_ber_rb->regs, 0x18);
            for (int i = 0; i < channel_count; i++)
            {
                if (channel_mask & (1 << i))
                {
                    float bits = ((float)mqnic_reg_read32(tdma_ber_rb->regs, 0x48 + i*16)) * bits_per_update;
                    float errs = mqnic_reg_read32(tdma_ber_rb->regs, 0x4C + i*16);
                    float ber = bits ? errs/bits/3 : 0;
                    printf("%1.4e  ", bits);
                    printf("%1.4e  ", errs);
                    printf("%1.4e  ", ber);
                }
            }

            printf("\n");
        }
    }

err:

    mqnic_close(dev);

    return 0;
}
