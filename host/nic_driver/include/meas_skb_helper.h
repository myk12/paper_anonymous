#ifndef MEAS_SKB_HELPER_H
#define MEAS_SKB_HELPER_H

#ifdef ENABLE_MEAS

#include <linux/skbuff.h>
#include <linux/etherdevice.h>
#include <linux/ip.h>
#include <linux/udp.h>
#include <linux/if_ether.h>
#include <linux/netdevice.h>
#include <linux/kernel.h>
#include <linux/ktime.h>
#include <linux/string.h>
#include <linux/errno.h>

#include "meas_hdr_kernel.h"

struct meas_skb_view {
	struct ethhdr *eth;
	struct iphdr *iph;
	struct udphdr *uh;
	struct meas_hdr_v1 *mh;
	unsigned int l2_len;
	unsigned int l3_len;
	unsigned int l4_len;
	unsigned int total_need;
};

static inline int meas_skb_pull_and_writable(struct sk_buff *skb, unsigned int len)
{
	if (!pskb_may_pull(skb, len))
		return -EINVAL;

	if (skb_try_make_writable(skb, len))
		return -ENOMEM;

	return 0;
}

static inline int meas_skb_locate_udp_hdr(struct sk_buff *skb, struct meas_skb_view *v)
{
	int ret;

	memset(v, 0, sizeof(*v));

	v->l2_len = ETH_HLEN;
	v->l4_len = sizeof(struct udphdr);

	ret = meas_skb_pull_and_writable(
		skb,
		v->l2_len + sizeof(struct iphdr) + v->l4_len + sizeof(struct meas_hdr_v1)
	);
	if (ret)
		return ret;

	skb_reset_mac_header(skb);

	v->eth = eth_hdr(skb);
	if (!v->eth)
		return -EINVAL;

	if (v->eth->h_proto != htons(ETH_P_IP))
		return -EPROTONOSUPPORT;

	if (skb->len < v->l2_len + sizeof(struct iphdr))
		return -EINVAL;

	v->iph = (struct iphdr *)(skb->data + v->l2_len);

	if (v->iph->version != 4)
		return -EPROTONOSUPPORT;

	if (v->iph->protocol != IPPROTO_UDP)
		return -EPROTONOSUPPORT;

	v->l3_len = v->iph->ihl * 4;
	if (v->l3_len < sizeof(struct iphdr))
		return -EINVAL;

	v->total_need = v->l2_len + v->l3_len + v->l4_len + sizeof(struct meas_hdr_v1);

	ret = meas_skb_pull_and_writable(skb, v->total_need);
	if (ret)
		return ret;

	skb_reset_mac_header(skb);

	v->eth = eth_hdr(skb);
	if (!v->eth || v->eth->h_proto != htons(ETH_P_IP))
		return -EINVAL;

	v->iph = (struct iphdr *)(skb->data + v->l2_len);
	v->uh  = (struct udphdr *)(skb->data + v->l2_len + v->l3_len);
	v->mh  = (struct meas_hdr_v1 *)(skb->data + v->l2_len + v->l3_len + v->l4_len);

	return 0;
}

static inline int meas_skb_find_hdr(struct sk_buff *skb, struct meas_hdr_v1 **mh_out)
{
	struct meas_skb_view v;
	int ret;

	ret = meas_skb_locate_udp_hdr(skb, &v);
	if (ret)
		return ret;

	if (!meas_hdr_v1_basic_ok(v.mh))
		return -ENOENT;

	*mh_out = v.mh;
	return 0;
}

static inline void mqnic_meas_stamp_tx_t2(struct sk_buff *skb)
{
	struct meas_hdr_v1 *mh;
	u64 t2;
	int ret;

	ret = meas_skb_find_hdr(skb, &mh);
	if (ret)
		return;

	t2 = ktime_get_real_ns();
	meas_write_t2(mh, t2);
}

static inline void mqnic_meas_stamp_rx_t7(struct sk_buff *skb)
{
	struct meas_hdr_v1 *mh;
	u64 t7;
	int ret;

	ret = meas_skb_find_hdr(skb, &mh);
	if (ret)
		return;

	t7 = ktime_get_real_ns();
	meas_write_t7(mh, t7);
}

#else  /* !ENABLE_MEAS */

#include <linux/skbuff.h>

static inline void mqnic_meas_stamp_tx_t2(struct sk_buff *skb)
{
	(void)skb;
}

static inline void mqnic_meas_stamp_rx_t7(struct sk_buff *skb)
{
	(void)skb;
}

#endif /* ENABLE_MEAS */

#endif /* MEAS_SKB_HELPER_H */
