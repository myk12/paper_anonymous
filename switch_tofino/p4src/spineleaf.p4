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

// -------------------------------
// Modes
// -------------------------------
const bit<8>    SWITCH_MODE_L2        = 0;
const bit<8>    SWITCH_MODE_L3        = 1;   // v1 Clos (no ECMP)
const bit<8>    SWITCH_MODE_NOPAXOS   = 2;   // NOPaxos switch mode

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
const bit<64> SEQUENCER_ID = 64w0xDEADBEEFDEADBEEF;

// OUM header format
// FRAG_MAGIC(32) | header_len(32) | orig_udp_src(16) |
// session_id(64) | ngroups(32) |
// group1_id(32)  | group1_seq(64) | ... (up to MAX_GROUPS groups)

// V1: parse + rewrite only 1 group for simplicity, and we only support non-fragmented packets (magic number + header_len check).
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

const bit<8> NOPAXOS_SEQ_HOST = 1;
const bit<8> NOPAXOS_SEQ_TOFINO = 2; 
const bit<8> NOPAXOS_SEQ_MODE = NOPAXOS_SEQ_TOFINO; // default to Tofino sequencer for simplicity

struct metadata_t {
    //pktgen_timer_header_t pktgen_timer_hdr;

    // --------------------------------
    // Switch configuration 
    // --------------------------------
    bit<8>      cfg_key;
    bit<8>      cfg_switch_mode; // 0 = SWITCH_MODE_L2, 1 = SWITCH_MODE_L3, 2 = SWITCH_MODE_NOPAXOS

    // -------------------------------
    //  Routing metadata
    // --------------------------------
    // We classify ingress ports into 3 categories: leaf downlink, leaf uplink, and spine port.
    bit<8> ingress_kind;    // K_UNKNOWN, K_LEAF_DOWNLINK, K_LEAF_UPLINK, K_SPINE_PORT
    bit<8> ingress_leaf;    // if ingress_kind is K_LEAF_DOWNLINK or K_LEAF_UPLINK, which leaf switch it is (1-4)
    bit<8> ingress_spine;   // if ingress_kind is K_SPINE_PORT, which spine switch it is (1-2)

    // For SWITCH_MODE_L3 and SWITCH_MODE_NOPAXOS, we classify the destination based on dst MAC to determine:
    bit<8> dst_leaf;        // if destination is a leaf, which leaf switch it is (1-4)
    PortId_t dst_downlink_port; // if destination is a leaf, which downlink port to forward to

    // ECMP-like spine selection metadata
    //bit<32> hash_seed;      // seed for hash calculation for spine selection
    //bit<8> selected_spine;  // selected spine switch based on hash

    bit<1>  nopaxos_override;
}

// Sequencer state: per-group 64-bit sequence number.
// Index type is bit<32> to match the group_id field in the OUM header.
const bit<32> MAX_GROUP_ID = 32w16; // support up to 16 groups for simplicity
Register<bit<64>, bit<32>>(MAX_GROUP_ID) nopaxos_oum_seq_reg;

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
            //6: parse_pktgen; // Port 6 is pktgen port
            //68: parse_pktgen; // Port 68 is pktgen port (DHCP)
            default: parse_ethernet;
        }
    }

    // if it's from pktgen port, extract pktgen header
    //state parse_pktgen {
    //    packet.extract(ig_md.pktgen_timer_hdr);
    //    transition parse_ethernet;
    //}

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
        transition select(hdr.udp.dst_port, hdr.ipv4.dst_addr) {
            (UDP_PORT_NOPAXOS, GROUP_ADDR) : parse_nopaxos_oum; // If it's destined to NOPaxos UDP port, parse OUM header
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
    // Switch configuration table (single entry for simplicity)
    // In a real implementation, you might want to have more complex configuration
    // logic with multiple entries and more sophisticated keys (e.g., based on port, VLAN, etc.)
    // -------------------------------
    action set_switch_config(bit<8> switch_mode) {
        ig_md.cfg_switch_mode = switch_mode;
    }

    table t_switch_cfg {
        key = {
            ig_md.cfg_key: exact;
        }
        actions = {
            set_switch_config;
            NoAction;
        }
        size = 1;
        // IMPORTANT: safe default that doesn't kill bring-up
        default_action = set_switch_config(SWITCH_MODE_L2);
    }

    // -------------------------------
    // L2 actions / table
    // -------------------------------
    action l2_forward(PortId_t port) { set_ucast_port(port); }
    action l2_drop() { drop(); }

    table t_l2_forward {
        key = { hdr.ethernet.dst_addr: exact; }
        actions = { l2_forward; l2_drop; }
        size = 1024;
        // default_action intentionally left unspecified; BFRT can clear/override
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

    // -------------------------------
    // NOPaxos Host Sequencer Dispatching
    // -------------------------------

    // For host sequencer, we have two kind of packets to steer:
    // 1) Client requests: the client request must be redirected to the host sequencer for sequencing.
    // 2) Sequencer packets: these are packets that originate from the host sequencer are needed to be multicast to all replicas

    //  Since there exist no key fields in NOPaxos's OUM header which indicates whether a packet is from 
    //  the client or host sequencer, we distinguish these two types of packets based on the ingress ports. 

    //  This means the client, host sequencer, and replicas must be connected to different ports. In a real implementation, 
    //  you can also use other ways to distinguish the packets, e.g., based on VLAN or specific header fields, 
    //  which would allow more flexible connectivity patterns as long as the packets can be correctly classified. 

    action nopaxos_redirect_to_host_sequencer(PortId_t sequencer_port) {
        set_ucast_port(sequencer_port);
    }

    action nopaxos_multicast_to_replicas(MulticastGroupId_t mcast_grp, bit<16> rid) {
        ig_intr_tm_md.mcast_grp_a = mcast_grp;
        ig_intr_tm_md.rid = rid;
        ig_intr_tm_md.enable_mcast_cutthru = 1;
        ig_md.nopaxos_override = 1; // set override bit to skip normal forwarding for sequencer-originated packets
    }

    table t_nopaxos_host_sequencer {
        key = {
            ig_intr_md.ingress_port: exact;
            hdr.ipv4.isValid() : exact;
            hdr.udp.isValid() : exact;
            hdr.ipv4.dst_addr: exact;
            hdr.udp.dst_port: exact;
        }

        actions = {
            nopaxos_redirect_to_host_sequencer;
            nopaxos_multicast_to_replicas;
            NoAction;
        }

        size = 64;
        default_action = NoAction();
    }

    // --------------------------------
    // NOPaxos Tofino Sequencer Dispatching
    // --------------------------------
    
    // For Tofino sequencer, we directly rewrite the OUM header and UDP src port in the switch to achieve sequencing 
    //  and replica notification without needing to send packets to an external host sequencer.
    action nopaxos_tofino_sequencer(MulticastGroupId_t mcast_grp, bit<16> rid) {
        // Sequence the packet by writing to the OUM header and modifying UDP src port.
        // 1) Save original UDP src port into OUM header
        hdr.nopaxos_oum.orig_udp_src = hdr.udp.src_port;

        // 2) Write sequencer ID into session_id field
        hdr.nopaxos_oum.session_id = SEQUENCER_ID;

        // For simplicity we only support 1 group in this example, so we set ngroups to 1 and only write group0_id and group0_seq.
        hdr.nopaxos_oum.ngroups = 1;
        // 3) Assign sequence number
        bit<32> group_id = hdr.nopaxos_oum.group0_id;
        bit<64> seq_num = nopaxos_oum_seq_reg.read(group_id);
        hdr.nopaxos_oum.group0_seq = seq_num;
        nopaxos_oum_seq_reg.write(group_id, seq_num + 1);

        // V1: gid must be < 16
        // 4) Set UDP src bitmap based on original UDP src port
        // since now we only use 1 group, the bitmap is simply 1 << group_id
        bit<16> bitmap = (bit<16>)(16w1 << (bit<4>)(hdr.nopaxos_oum.group0_id));
        hdr.udp.src_port = bitmap;
        hdr.udp.checksum = 0; // For simplicity we skip checksum recalculation in this example. 

        // 5) Multicast to replicas
        ig_intr_tm_md.mcast_grp_a = mcast_grp;
        ig_intr_tm_md.rid = rid;
        ig_intr_tm_md.enable_mcast_cutthru = 1;
        ig_md.nopaxos_override = 1; // set override bit to skip normal forwarding for sequencer-originated packets
    }

    table t_nopaxos_tofino_sequencer {
        key = {
            ig_intr_md.ingress_port: exact;
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
        size = 64;
        default_action = NoAction();
    }

    // -------------------------------
    // Apply
    // -------------------------------
    apply {
        // ---- Initialization ----
        ig_md.nopaxos_override = 0; // default to no override for NOPaxos processing
        ig_md.dst_leaf = 0;
        ig_md.dst_downlink_port = (PortId_t)0;
        ig_md.ingress_kind = K_UNKNOWN;
        ig_md.ingress_leaf = 0;
        ig_md.ingress_spine = 0;

        ig_md.cfg_switch_mode = SWITCH_MODE_L2; // default to L2 mode

        // ---- configuration tables ----
        ig_md.cfg_key = 0; // For simplicity we use a fixed key for switch configuration. 
        t_switch_cfg.apply();

        // Must have ethernet for all modes
        if (!hdr.ethernet.isValid()) {
            drop();
            return;
        }

        // -------------------------
        // MODE_L2: only L2 table
        // -------------------------
        if (ig_md.cfg_switch_mode == SWITCH_MODE_L2) {
            t_l2_forward.apply();
            return;
        }

        // For MODE_L3 / MODE_NOPAXOS we need port role + dst classify
        t_port_role.apply();
        t_dst_mac_classify.apply();

        // -------------------------
        // MODE_NOPAXOS: NOPaxos sequencer dispatching
        // -------------------------
        if (ig_md.cfg_switch_mode == SWITCH_MODE_NOPAXOS) {
            if (NOPAXOS_SEQ_MODE == NOPAXOS_SEQ_TOFINO) {
                t_nopaxos_tofino_sequencer.apply();
            } else if (NOPAXOS_SEQ_MODE == NOPAXOS_SEQ_HOST) {
                t_nopaxos_host_sequencer.apply();
            } else {
                // If sequencer mode is not set, we treat it as normal forwarding. 
            }

            if (ig_md.nopaxos_override == 1) {
                // If the packet is marked for NOPaxos processing, we skip normal forwarding logic to avoid potential conflicts.
                return;
            }
        }

        // -------------------------
        // MODE_L3: v1 Clos forwarding (no ECMP)
        // -------------------------
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
