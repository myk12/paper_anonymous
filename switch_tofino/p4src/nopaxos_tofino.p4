/* ===============================================================
 *         Spine-Leaf Topology P4 Program for Tofino Switch
 * ===============================================================
 * This P4 program implements a basic spine-leaf switching logic
 * for a Tofino switch in a spine-leaf topology.
 * The program classifies packets based on their ingress port
 * to determine whether they are from leaf switches or spine switches,
 * and forwards them accordingly.
 * =============================================================== */

#include <core.p4>
#if __TARGET_TOFINO__ == 2
#include <t2na.p4>
#else
#include <tna.p4>
#endif

#include "include/headers.p4"
#include "include/util.p4"

//--------------------------------------------
// Constants and Type Definitions
//--------------------------------------------

#define ETHERTYPE_IPV4 0x0800
#define IP_PROTOCOL_UDP 17
#define IP_PROTOCOL_TCP 6

// Ingress kinds
const bit<8>    K_UNKNOWN       = 0;   // unknown/unspecified
const bit<8>    K_LEAF_DOWNLINK = 1;   // servers/FPGAs -> leaf switch
const bit<8>    K_LEAF_UPLINK   = 2;   // leaf switch -> spine
const bit<8>    K_SPINE_PORT    = 3;   // spine ports -> leaf uplinks

// -------------------------------
// Header and Metadata Definitions
// -------------------------------

// -------------------------------
// NOPaxos OUM Header
// -------------------------------
const bit<32>   NONFRAG_MAGIC = 32w0x18030520; //magic number for non-fragment NOPaxos packets
const bit<8>    MAX_GROUPS = 1;
const bit<16>   UDP_PORT_NOPAXOS = 16w12000; // UDP port for NOPaxos groupaddr traffic
const bit<32>   GROUP_ADDR = 32w0xB10000FF; // 177.0.0.255 - reserved group address for NOPaxos Phase-1 packets, can be changed as needed

// A fixed sequencer ID written into the OUM header (session ID field).
// This can also be made into configurable via a register or table if needed, but for simplicity we hardcode it here.
const bit<64> SEQUENCER_ID = 64w0x0000000000000000; // example: 0 for now, can be changed to other values if needed

// OUM header format
// FRAG_MAGIC(32) | header_len(32) | orig_udp_src(16) |
// session_id(64) | ngroups(32) |
// group1_id(32)  | group1_seq(64) | ... (up to MAX_GROUPS groups)

header nopaxos_oum_t {
    bit<32> magic;          // Magic number to identify NOPaxos OUM packets
    bit<32> header_len;     // Length of the OUM header
    bit<16> orig_udp_src;   // Original UDP source port of the Phase-1 packet
    bit<64> session_id;     // Session ID (we use this to store a fixed sequencer ID in v1)
    bit<32> ngroups;        // Number of groups in this OUM packet (should be 1 for our simplified version)
    bit<32> group0_id;      // Group ID (e.g., shard ID for NOPaxos)
    bit<64> group0_seq;     // Sequence number for this group
}

struct header_t {
    ethernet_h      ethernet;
    ipv4_h          ipv4;
    udp_h           udp;
    tcp_h           tcp;
    nopaxos_oum_t   nopaxos_oum; // Optional NOPaxos OUM header, only valid if magic number matches
}

struct metadata_t {
    // -------------------------------
    //  Routing metadata
    // --------------------------------
    pktgen_timer_header_t pktgen_timer_hdr; // Metadata for pktgen timer header, used for testing and timestamping

    // We classify ingress ports into 3 categories: leaf downlink, leaf uplink, and spine port.
    bit<8> ingress_kind;    // K_UNKNOWN, K_LEAF_DOWNLINK, K_LEAF_UPLINK, K_SPINE_PORT
    bit<8> ingress_leaf;    // if ingress_kind is K_LEAF_DOWNLINK or K_LEAF_UPLINK, which leaf switch it is (1-4)
    bit<8> ingress_spine;   // if ingress_kind is K_SPINE_PORT, which spine switch it is (1-2)

    // For SWITCH_MODE_L3 and SWITCH_MODE_NOPAXOS, we classify the destination based on dst MAC to determine:
    bit<8> dst_leaf;        // if destination is a leaf, which leaf switch it is (1-4)
    PortId_t dst_downlink_port; // if destination is a leaf, which downlink port to forward to

    //bit<32> hash_seed;      // seed for hash calculation for spine selection
    //bit<8> selected_spine;  // selected spine switch based on hash

    bit<1>  nopaxos_override;
    bit<16> nopaxos_bitmap;  // bitmap of UDP src ports to notify
}

// --------------------------------------------
// Ingress Parser
// --------------------------------------------
parser IngressParser(
    packet_in packet,
    out header_t hdr,
    out metadata_t ig_md,
    out ingress_intrinsic_metadata_t ig_intr_md
) {
    TofinoIngressParser() tofino_parser;

    state start {
        tofino_parser.apply(packet, ig_intr_md);

        // Here we handle pktgen headers based on ingress port
        transition select(ig_intr_md.ingress_port) {
            6: parse_pktgen; // Port 6 is pktgen port
            68: parse_pktgen; // Port 68 is pktgen port (DHCP)
            default: parse_ethernet;
        }
    }

    // if it's from pktgen port, extract pktgen header
    state parse_pktgen {
        packet.extract(ig_md.pktgen_timer_hdr);
        transition parse_ethernet;
    }

    // normal ethernet parsing
    state parse_ethernet {
        packet.extract(hdr.ethernet);
        transition select(hdr.ethernet.ether_type) {
            ETHERTYPE_IPV4: parse_ipv4;
            default: accept;
        }
    }

    // IPv4 parsing
    state parse_ipv4 {
        packet.extract(hdr.ipv4);
        transition select(hdr.ipv4.protocol) {
            IP_PROTOCOL_UDP: parse_udp;
            IP_PROTOCOL_TCP: parse_tcp;
            default: accept;
        }
    }

    // UDP parsing
    state parse_udp {
        packet.extract(hdr.udp);
        // Check destination port for potential NOPaxos Phase-1 traffic
        transition select(hdr.udp.dst_port) {
            UDP_PORT_NOPAXOS : parse_nopaxos_oum; // If it's destined to NOPaxos UDP port, parse OUM header
            default: accept;
        }
    }

    // TCP parsing
    state parse_tcp {
        packet.extract(hdr.tcp);
        transition accept;
    }

    // NOPaxos OUM parsing
    state parse_nopaxos_oum {
        packet.extract(hdr.nopaxos_oum);
        transition accept;
    }
}

control Ingress(
    inout header_t hdr,
    inout metadata_t ig_md,
    in ingress_intrinsic_metadata_t ig_intr_md,
    in ingress_intrinsic_metadata_from_parser_t ig_intr_prsr_md,
    inout ingress_intrinsic_metadata_for_deparser_t ig_intr_dprsr_md,
    inout ingress_intrinsic_metadata_for_tm_t ig_intr_tm_md)
{
    // -------------------------------
    // Helpers
    // -------------------------------
    action drop() {
        ig_intr_dprsr_md.drop_ctl = 0x1;
    }

    action set_ucast_port(PortId_t port) {
        ig_intr_tm_md.ucast_egress_port = port;
    }

    // -------------------------------
    // Port role (required for MODE_L3 / MODE_NOPAXOS)
    // -------------------------------
    action set_port_role(bit<8> kind, bit<8> leaf_id, bit<8> spine_id) {
        ig_md.ingress_kind = kind;
        ig_md.ingress_leaf = leaf_id;
        ig_md.ingress_spine = spine_id;
    }

    table t_port_role {
        key = { ig_intr_md.ingress_port: exact; }
        actions = { set_port_role; NoAction; }
        size = 64;
        // IMPORTANT: safe default that doesn't kill bring-up
        // If you want strictness later, you can change this to drop().
        default_action = set_port_role(K_UNKNOWN, 0, 0);
    }

    // -------------------------------
    // Destination classification (MAC -> dst_leaf + dst_downlink_port)
    // Used by MODE_L3 and MODE_NOPAXOS
    // -------------------------------
    action set_dst(bit<8> dst_leaf, PortId_t dst_downlink_port) {
        ig_md.dst_leaf = dst_leaf;
        ig_md.dst_downlink_port = dst_downlink_port;
    }

    table t_dst_mac_classify {
        key = { hdr.ethernet.dst_addr: exact; }
        actions = { set_dst; }
        size = 1024;
        default_action = set_dst(0, (PortId_t)0);
    }

    // -------------------------------
    // v1 CLOS forwarding tables (NO ECMP)
    //
    // leaf_downlink -> remote leaf: send to fixed uplink port for this leaf
    // key only needs ingress_leaf because each leaf connects to exactly one spine uplink in v1.
    // -------------------------------
    action set_leaf_uplink_port(PortId_t uplink_port) {
        set_ucast_port(uplink_port);
    }

    table t_leaf_uplink_v1 {
        key = { ig_md.ingress_leaf: exact; }
        actions = { set_leaf_uplink_port; }
        size = 16;
        default_action = set_leaf_uplink_port((PortId_t)0);
    }

    // spine ingress -> dst_leaf : send out the spine port that leads to that leaf
    action set_spine_egress_port(PortId_t spine_port) {
        set_ucast_port(spine_port);
    }

    table t_spine_forward {
        key = {
            ig_md.ingress_spine: exact;
            ig_md.dst_leaf: exact;
        }
        actions = { set_spine_egress_port; }
        size = 64;
        default_action = set_spine_egress_port((PortId_t)0);
    }

    // --------------------------------
    // NOPaxos Tofino Sequencer Dispatching
    // --------------------------------
    // Sequencer state: per-group 64-bit sequence number.
    // Index type is bit<32> to match the group_id field in the OUM header.
    const bit<32> MAX_GROUP_ID = 32w16; // support up to 16 groups for simplicity
    Register<bit<64>, bit<32>>(size = MAX_GROUP_ID, initial_value = 0) nopaxos_oum_seq_reg;
    RegisterAction<bit<64>, bit<32>, bit<64>>(nopaxos_oum_seq_reg) oum_seq_fetch_add = {
        void apply(inout bit<64> value, out bit<64> rv) {
            value = value + 1;
            rv = value;
        }
    };

    // For Tofino sequencer, we directly rewrite the OUM header and UDP src port in the switch to achieve sequencing 
    //  and replica notification without needing to send packets to an external host sequencer.
    action nopaxos_tofino_sequencer(MulticastGroupId_t mcast_grp, bit<16> rid) {
        // Sequence the packet by writing to the OUM header and modifying UDP src port.
        // 1) Save original UDP src port into OUM header
        hdr.nopaxos_oum.orig_udp_src = hdr.udp.src_port;

        // 2) Write sequencer ID into session_id field
        hdr.nopaxos_oum.session_id = SEQUENCER_ID;

        // 3) Assign sequence number
        bit<64> seq_num = oum_seq_fetch_add.execute(0); // atomically fetch and increment sequence number for the specified group
        // change endianess if needed - Tofino registers are little-endian, but we want to keep the OUM header in network byte order (big-endian) for easier parsing by software components. So we convert the sequence number to big-endian before writing to the header.
        hdr.nopaxos_oum.group0_seq = seq_num;

        // 4) Modify UDP src port to encode the bitmap of replicas to notify
        // In a real implementation, the bitmap can be determined based on the group ID and other factors. For simplicity, we take it as an action parameter here.
        // Note: since the OUM header is only present in packets destined to the NOPaxos UDP port, we can safely reuse the UDP src port field to carry the bitmap for replica notification without affecting normal traffic.
        hdr.udp.src_port = 16w1;
        hdr.udp.checksum = 0;

        // For simplicity we assume all sequenced packets belong to the same group and are multicast to the same group address.
        ig_intr_tm_md.mcast_grp_a = mcast_grp;
        ig_intr_tm_md.rid = rid;
        ig_intr_tm_md.enable_mcast_cutthru = 1;
        ig_md.nopaxos_override = 1; // set override bit to skip normal forwarding for sequencer-originated packets
    }

    table t_nopaxos_tofino_sequencer {
        key = {
            ig_intr_md.ingress_port: exact; // port where client request comes in
            hdr.ipv4.isValid() : exact;
            hdr.udp.isValid() : exact;
            hdr.ipv4.dst_addr: exact;
            hdr.udp.dst_port: exact;
            hdr.nopaxos_oum.isValid() : exact;
        }
        actions = {
            nopaxos_tofino_sequencer;
            NoAction;
        }
        size = 16;
        default_action = NoAction();
    }

    action set_gid_to_bitmap(bit<16> bitmap) {
        ig_md.nopaxos_bitmap = bitmap;
    }

    table t_gid_to_bitmap {
        key = {
            hdr.nopaxos_oum.group0_id: exact;
        }
        actions = {
            set_gid_to_bitmap;
        }
        size = 16;
        default_action = set_gid_to_bitmap(16w0); // default to empty bitmap for unknown groups
    }

    // -------------------------------
    // Apply
    // -------------------------------
    apply {
        ig_md.nopaxos_override = 0; // default to no override

        // Must have ethernet for all modes
        if (!hdr.ethernet.isValid()) {
            drop();
            return;
        }

        // -------------------------
        // MODE_NOPAXOS: NOPaxos sequencer dispatching
        // -------------------------
        if (hdr.ipv4.isValid() && hdr.udp.isValid() && hdr.udp.dst_port == UDP_PORT_NOPAXOS) {
            //t_gid_to_bitmap.apply(); // lookup group ID to get bitmap of replicas to notify

            t_nopaxos_tofino_sequencer.apply();

            if (ig_md.nopaxos_override == 1) {
                // If the packet is marked for NOPaxos processing, we skip normal forwarding logic to avoid potential conflicts.
                return;
            }
        }

        // -------------------------
        // MODE_L3: v1 Clos forwarding (no ECMP)
        // -------------------------
        t_port_role.apply();

        t_dst_mac_classify.apply();

        // Same leaf: directly downlink
        if (ig_md.dst_leaf != 0 && ig_md.dst_leaf == ig_md.ingress_leaf) {
            set_ucast_port(ig_md.dst_downlink_port);
            return;
        }

        // From leaf downlink: send to this leaf's single uplink
        if (ig_md.ingress_kind == K_LEAF_DOWNLINK) {
            t_leaf_uplink_v1.apply();
            // If uplink_port is 0, treat as drop
            if (ig_intr_tm_md.ucast_egress_port == (PortId_t)0) {
                drop();
            }
            return;
        }

        // From spine: forward based on dst_leaf
        if (ig_md.ingress_kind == K_SPINE_PORT) {
            t_spine_forward.apply();
            if (ig_intr_tm_md.ucast_egress_port == (PortId_t)0) {
                drop();
            }
            return;
        }

        // From leaf uplink: in v1, this typically means traffic coming back from spine into a leaf-uplink-facing port.
        // For simplicity, send to downlink decided by dst_mac_classify.
        if (ig_md.ingress_kind == K_LEAF_UPLINK) {
            set_ucast_port(ig_md.dst_downlink_port);
            return;
        }

        drop();
        return;
    }
}

// -------------------------------------------
// Ingress Deparser - keep it simple
// -------------------------------------------
control IngressDeparser(
    packet_out packet,
    inout header_t hdr,
    in metadata_t ig_md,
    in ingress_intrinsic_metadata_for_deparser_t ig_intr_dprsr_md
) {
    apply {
        // Emit headers
        packet.emit(hdr.ethernet);
        packet.emit(hdr.ipv4);
        packet.emit(hdr.udp);
        packet.emit(hdr.tcp);
        packet.emit(hdr.nopaxos_oum);
    }
}

// -------------------------------------------
// Egress Parser & Deparser
// -------------------------------------------

struct my_egress_metadata_t {
    // Add any egress-specific metadata fields if needed
}

parser EgressParser(
    packet_in packet,
    out header_t hdr,
    out my_egress_metadata_t eg_md,
    out egress_intrinsic_metadata_t eg_intr_md
) {
    TofinoEgressParser() tofino_parser;

    state start {
        tofino_parser.apply(packet, eg_intr_md);
        transition parse_ethernet;
    }

    state parse_ethernet {
        packet.extract(hdr.ethernet);
        transition select(hdr.ethernet.ether_type) {
              ETHERTYPE_IPV4: parse_ipv4;
              default: accept;
        }
    }

    state parse_ipv4 {
        packet.extract(hdr.ipv4);
        transition select(hdr.ipv4.protocol) {
            IP_PROTOCOL_UDP: parse_udp;
            IP_PROTOCOL_TCP: parse_tcp;
            default: accept;
        }
    }

    state parse_udp {
        packet.extract(hdr.udp);
        transition accept;
    }

    state parse_tcp {
        packet.extract(hdr.tcp);
        transition accept;
    }

    state parse_nopaxos_oum {
        bit<32> magic = packet.lookahead<bit<32>>();
        transition select(magic) {
            NONFRAG_MAGIC: extract_nopaxos_oum; // If magic number matches, extract OUM header
            default: accept; // Otherwise, treat as normal UDP traffic
        }
    }

    state extract_nopaxos_oum {
        packet.extract(hdr.nopaxos_oum);
        transition accept;
    }
}

// -------------------------------------------
// Egress Control: Timestamping Logic
// -------------------------------------------
control Egress(
    inout header_t hdr,
    inout my_egress_metadata_t eg_md,
    in egress_intrinsic_metadata_t eg_intr_md,
    in egress_intrinsic_metadata_from_parser_t eg_intr_from_prsr,
    inout egress_intrinsic_metadata_for_deparser_t eg_intr_md_for_dprsr,
    inout egress_intrinsic_metadata_for_output_port_t eg_intr_md_for_oport
) {
    apply {}
}

control EgressDeparser(
    packet_out packet,
    inout header_t hdr,
    in my_egress_metadata_t eg_md,
    in egress_intrinsic_metadata_for_deparser_t eg_intr_dprsr_md
) {
    apply {
        packet.emit(hdr.ethernet);
        packet.emit(hdr.ipv4);
        packet.emit(hdr.udp);
        packet.emit(hdr.tcp);
        packet.emit(hdr.nopaxos_oum);
    }
}

// -------------------------------------------
// Main Pipeline and Switch Declaration
// -------------------------------------------
Pipeline(IngressParser(),
    Ingress(),
    IngressDeparser(),
    EgressParser(),
    Egress(),
    EgressDeparser()) pipe;
Switch(pipe) main;
