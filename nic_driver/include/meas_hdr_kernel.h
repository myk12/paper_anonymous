#ifndef MEAS_HDR_KERNEL_H
#define MEAS_HDR_KERNEL_H

#include <linux/types.h>
#include <linux/byteorder/generic.h>
#include <linux/bitops.h>
#include <linux/kernel.h>

#define MEAS_MAGIC      0x4d454153u  /* 'MEAS' */
#define MEAS_HDR_VER_V1 1

enum meas_clock_domain {
	MEAS_CLK_UNSPEC      = 0,
	MEAS_CLK_HOST_MONO   = 1,
	MEAS_CLK_NIC_LOCAL   = 2,
	MEAS_CLK_FABRIC_SYNC = 3,
	MEAS_CLK_GLOBAL_PTP  = 4,
};

/* valid_bitmap */
#define MEAS_V_T1           BIT(0)
#define MEAS_V_T2           BIT(1)
#define MEAS_V_T3           BIT(2)
#define MEAS_V_T4           BIT(3)
#define MEAS_V_T5           BIT(4)
#define MEAS_V_T6           BIT(5)
#define MEAS_V_T7           BIT(6)
#define MEAS_V_T8           BIT(7)
#define MEAS_V_SRC_DEV_ID   BIT(8)
#define MEAS_V_SW_ID        BIT(9)
#define MEAS_V_QUEUE_META   BIT(10)

/* flags */
#define MEAS_F_SWITCH_TOUCHED   BIT(0)
#define MEAS_F_NIC_TX_PATH      BIT(1)
#define MEAS_F_NIC_RX_PATH      BIT(2)
#define MEAS_F_FALLBACK_PATH    BIT(3)
#define MEAS_F_TIME_UNSYNCED    BIT(4)
#define MEAS_F_TRUNCATED_HDR    BIT(5)
#define MEAS_F_REPLY_PACKET     BIT(6)

/* error_bitmap */
#define MEAS_E_HDR_TOO_SHORT      BIT(0)
#define MEAS_E_BAD_MAGIC          BIT(1)
#define MEAS_E_NOT_WRITABLE       BIT(2)
#define MEAS_E_CSUM_CONFLICT      BIT(3)
#define MEAS_E_CLOCK_UNAVAILABLE  BIT(4)
#define MEAS_E_PIPELINE_BYPASS    BIT(5)
#define MEAS_E_UNSUPPORTED_VER    BIT(6)
#define MEAS_E_PARSE_FAIL         BIT(7)

struct meas_hdr_v1 {
	__be32 magic;
	__be16 ver;
	__be16 hdr_len;

	__be64 req_id;

	__be32 valid_bitmap;
	__be16 clock_domain;
	__be16 flags;

	__be64 T1; /* user send */
	__be64 T2; /* TX driver */
	__be64 T3; /* src NIC ingress */
	__be64 T4; /* Tofino ingress */
	__be64 T5; /* Tofino egress */
	__be64 T6; /* dst NIC egress-to-host boundary */
	__be64 T7; /* RX driver */
	__be64 T8; /* user receive */

	__be32 src_dev_id;
	__be32 sw_id;

	__be32 queue_meta;
	__be32 error_bitmap;
} __packed;

#define MEAS_HDR_V1_LEN ((u16)sizeof(struct meas_hdr_v1))

static inline bool meas_hdr_v1_magic_ok(const struct meas_hdr_v1 *mh)
{
	return mh && mh->magic == cpu_to_be32(MEAS_MAGIC);
}

static inline bool meas_hdr_v1_ver_ok(const struct meas_hdr_v1 *mh)
{
	return mh && mh->ver == cpu_to_be16(MEAS_HDR_VER_V1);
}

static inline bool meas_hdr_v1_len_ok(const struct meas_hdr_v1 *mh)
{
	return mh && be16_to_cpu(mh->hdr_len) >= sizeof(struct meas_hdr_v1);
}

static inline bool meas_hdr_v1_basic_ok(const struct meas_hdr_v1 *mh)
{
	return meas_hdr_v1_magic_ok(mh) &&
	       meas_hdr_v1_ver_ok(mh) &&
	       meas_hdr_v1_len_ok(mh);
}

static inline void meas_or_valid(__be32 *valid_bitmap, u32 bits)
{
	u32 v = be32_to_cpu(*valid_bitmap);
	v |= bits;
	*valid_bitmap = cpu_to_be32(v);
}

static inline void meas_or_flags(__be16 *flags, u16 bits)
{
	u16 v = be16_to_cpu(*flags);
	v |= bits;
	*flags = cpu_to_be16(v);
}

static inline void meas_or_error(__be32 *error_bitmap, u32 bits)
{
	u32 v = be32_to_cpu(*error_bitmap);
	v |= bits;
	*error_bitmap = cpu_to_be32(v);
}

static inline void meas_write_t2(struct meas_hdr_v1 *mh, u64 t2_ns)
{
	mh->T2 = cpu_to_be64(t2_ns);
	meas_or_valid(&mh->valid_bitmap, MEAS_V_T2);
	meas_or_flags(&mh->flags, MEAS_F_NIC_TX_PATH);
}

static inline void meas_write_t4(struct meas_hdr_v1 *mh, u64 t4_ns)
{
	mh->T4 = cpu_to_be64(t4_ns);
	meas_or_valid(&mh->valid_bitmap, MEAS_V_T4);
	meas_or_flags(&mh->flags, MEAS_F_SWITCH_TOUCHED);
}

static inline void meas_write_t5(struct meas_hdr_v1 *mh, u64 t5_ns)
{
	mh->T5 = cpu_to_be64(t5_ns);
	meas_or_valid(&mh->valid_bitmap, MEAS_V_T5);
	meas_or_flags(&mh->flags, MEAS_F_SWITCH_TOUCHED);
}

static inline void meas_write_t7(struct meas_hdr_v1 *mh, u64 t7_ns)
{
	mh->T7 = cpu_to_be64(t7_ns);
	meas_or_valid(&mh->valid_bitmap, MEAS_V_T7);
	meas_or_flags(&mh->flags, MEAS_F_NIC_RX_PATH);
}

#endif /* MEAS_HDR_KERNEL_H */
