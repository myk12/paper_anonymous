// =============================================================================
//             Hybrid EPS-OCS P4 program for Tofino
// =============================================================================

// This P4 program implements a hybrid architecture that combines the features of
// both EPS (Electrical Packet Switching) and OCS (Optical Circuit Switching).

#include <core.p4>
#if __TARGET_TOFINO__ == 2
#include <t2na.p4>
#else
#include <tna.p4>
#endif

#include "include/headers.p4"
#include "include/util.p4"

// ---------------------------------------------------------------
//  Constants and Type Definitions
// ---------------------------------------------------------------
#define ETHERTYPE_IPV4 0x0800
#define IP_PROTOCOL_UDP 17
#define IP_PROTOCOL_TCP 6

#define MEAS_UDP_PORT 1997
#define HOPVAR_UDP_PORT 1999

// N = 8 slots
const bit<48> OCS_SLOT_MASK = 48w7; // Mask to extract slot ID from port number
const bit<48> OCS_SLOT_SHIFT = 13; // Number of bits to shift to get slot ID

struct header_t {
    ethernet_h ethernet;
    ipv4_h ipv4;
    tcp_h tcp;
    udp_h udp;
    meas_hdr_h meas;        // optional measurement header
    hopvar_hdr_h hopvar;    // optional hop variable header
}

struct metadata_t {
    bit<1> is_ocs;  // 0 = EPS, 1 = OCS
    // for OCS
    bit<3> slot_id;
    bit<1> selected_spine; // ECMP: 0 = spine 1, 1 = spine 2
    pktgen_timer_header_t pktgen_timer_hdr; // Pktgen timer header
};

// ---------------------------------------------------------------
//              Ingress Logic
// ---------------------------------------------------------------

// Ingress Parser
parser IngressParser(packet_in packet,
                    out header_t hdr,
                    out metadata_t ig_md,
                    out ingress_intrinsic_metadata_t ig_intr_md)
{
    TofinoIngressParser() tofino_parser;

    state start {
        tofino_parser.apply(packet, ig_intr_md);
        transition select(ig_intr_md.ingress_port) {
            6: parse_pktgen; // Port 6 is pktgen port
            default: parse_ethernet;
        }
    }

    state parse_pktgen {
        packet.extract(ig_md.pktgen_timer_hdr);
        transition parse_ethernet;
    }

    state parse_ethernet {
        packet.extract(hdr.ethernet);
        transition select(hdr.ethernet.ether_type) {
            ETHERTYPE_IPV4: parse_ipv4;
            ETHERTYPE_ARP: accept;
            default: accept;
        }
    }

    state parse_ipv4 {
        packet.extract(hdr.ipv4);
        transition select(hdr.ipv4.protocol) {
            IP_PROTOCOL_TCP: parse_tcp;
            IP_PROTOCOL_UDP: parse_udp;
            default: accept;
        }
    }

    state parse_tcp {
        packet.extract(hdr.tcp);
        transition accept;
    }

    state parse_udp {
        packet.extract(hdr.udp);
        transition select(hdr.udp.dst_port) {
            MEAS_UDP_PORT: parse_meas;
            HOPVAR_UDP_PORT: parse_hopvar;
            default: accept;
        }
    }

    state parse_meas {
        packet.extract(hdr.meas);
        transition accept;
    }

    state parse_hopvar {
        packet.extract(hdr.hopvar);
        transition accept;
    }
}

// Ingress Control
control Ingress(
    inout header_t hdr,
    inout metadata_t ig_md,
    in ingress_intrinsic_metadata_t ig_intr_md,
    in ingress_intrinsic_metadata_from_parser_t ig_intr_prsr_md,
    inout ingress_intrinsic_metadata_for_deparser_t ig_intr_dprsr_md,
    inout ingress_intrinsic_metadata_for_tm_t ig_intr_tm_md)
{
    // -----------------------------------
    // Helper Functions
    // -----------------------------------
    action drop() {
        ig_intr_dprsr_md.drop_ctl = 0x1; // Mark packet for dropping
    }

    action set_ucast_port(PortId_t port) {
        ig_intr_tm_md.ucast_egress_port = port;
    }

    // -----------------------------------
    //  Table for port classification
    // -----------------------------------
    action set_port_kind_eps() { ig_md.is_ocs = 0;}
    action set_port_kind_ocs() { ig_md.is_ocs = 1;}

    table t_set_port_kind {
        key = {
            ig_intr_md.ingress_port: exact;
        }
        actions = {
            set_port_kind_eps;
            set_port_kind_ocs;
            NoAction;
        }
        size = 256;
        default_action = set_port_kind_eps();
    }

    // -----------------------------------
    // Table for electrical path selection
    // -----------------------------------
    table t_eps_forward {
        key = {
            ig_intr_md.ingress_port: exact; 
            hdr.ethernet.dst_addr: exact;
            ig_md.selected_spine: exact;
        }

        actions = {
            set_ucast_port;
            drop;
            NoAction;
        }

        size = 4096;
        default_action = drop();
    }

    // -----------------------------------
    // Table for optical path selection
    // -----------------------------------

    table t_ocs_schedule {
        key = {
            ig_intr_md.ingress_port: exact;
            ig_md.slot_id: exact;
        }
        actions = {
            set_ucast_port;
            drop;
            NoAction;
        }
        size = 2048;
        default_action = drop();
    }

    // -----------------------------------
    // Table for measurement processing (optional)
    // -----------------------------------
    action meas_ingress_timestamp() {
        // T4 = packet enters Tofino ingress pipeline
        hdr.meas.ts4 = (bit<64>)ig_intr_md.ingress_mac_tstamp;
        
        // valid_bitmap |= MEAS_V_T4 (bit 3)
        hdr.meas.valid_bitmap = hdr.meas.valid_bitmap | 32w0x00000008;

        // flags |= MEAS_F_SWITCH_TOUCHED (bit 0)
        hdr.meas.flags = hdr.meas.flags | 16w0x0001;

        // simplify UDP checksum update by just setting it to 0 (since we're only adding a timestamp header, this is fine for testing)
        hdr.udp.checksum = 0;
    }

    table t_meas_ingress_timestamp {
        key = {
            ig_intr_md.ingress_port: exact;
            hdr.meas.isValid(): exact;
        }
        actions = {
            meas_ingress_timestamp;
            NoAction;
        }
        size = 256;
        default_action = NoAction();
    }

    // -----------------------------------
    // Sequencing of pktgen
    // -----------------------------------
    Register<bit<64>, bit<32>>(size = 10, initial_value = 0) pktgen_sequence_reg;
    RegisterAction<bit<64>, bit<32>, bit<64>>(pktgen_sequence_reg) pktgen_sequence_fetch_add = {
        void apply(inout bit<64> value, out bit<64> rv)
        {
            value = value + 1;
            rv = value;
        }
    };

    // -----------------------------------
    // Ingress Processing Logic
    // -----------------------------------
    apply {
        // default 
        ig_md.is_ocs = 0;
        ig_md.slot_id = 0;
        ig_md.selected_spine = 0;

        // classify port type (EPS vs OCS)
        t_set_port_kind.apply();

        if (hdr.meas.isValid()) {
            t_meas_ingress_timestamp.apply();
        }

        if (hdr.hopvar.isValid() && ig_intr_md.ingress_port == 6) {
            bit<64> seq_num;
            seq_num = pktgen_sequence_fetch_add.execute(0); // Increment sequence number for pktgen packets
            hdr.hopvar.req_id = seq_num;
        }

        if (hdr.hopvar.isValid()) {
            bit<48> ts = ig_intr_md.ingress_mac_tstamp;
            bit<16> ts_count = hdr.hopvar.ts_count;

            if (ts_count == 0) {
                hdr.hopvar.ts0 = ts;
            } else if (ts_count == 1) {
                hdr.hopvar.ts1 = ts;
            } else if (ts_count == 2) {
                hdr.hopvar.ts2 = ts;
            } else if (ts_count == 3) {
                hdr.hopvar.ts3 = ts;
            } else if (ts_count == 4) {
                hdr.hopvar.ts4 = ts;
            } else if (ts_count == 5) {
                hdr.hopvar.ts5 = ts;
            } else if (ts_count == 6) {
                hdr.hopvar.ts6 = ts;
            } else if (ts_count == 7) {
                hdr.hopvar.ts7 = ts;
            } else {
                hdr.hopvar.flags = hdr.hopvar.flags | 16w0x0001; // overflow
            }

            hdr.hopvar.ts_count = ts_count + 1;

            hdr.udp.checksum = 0;
        }


        if (ig_md.is_ocs == 1) {
            // compute slot id from global timestamp
            bit<48> ts = ig_intr_prsr_md.global_tstamp;
            ig_md.slot_id = (bit<3>)((ts >> OCS_SLOT_SHIFT) & OCS_SLOT_MASK);

            t_ocs_schedule.apply();

            return;
        }

        // EPS
        t_eps_forward.apply();
    }
}

// ---------------------------------------------------------------
//      Ingress Deparser
// ---------------------------------------------------------------
control IngressDeparser(packet_out packet,
                        inout header_t hdr,
                        in metadata_t ig_md,
                        in ingress_intrinsic_metadata_for_deparser_t ig_intr_dprsr_md)
{
    apply {
        // Emit headers
        packet.emit(hdr.ethernet);
        packet.emit(hdr.ipv4);
        packet.emit(hdr.udp);
        packet.emit(hdr.tcp);
        packet.emit(hdr.meas);
        packet.emit(hdr.hopvar);
    }
}

// ---------------------------------------------------------------
//              Egress Parser
// ---------------------------------------------------------------
parser EgressParser(packet_in packet,
                    out header_t hdr,
                    out metadata_t eg_md,
                    out egress_intrinsic_metadata_t eg_intr_md)
{
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
        transition select(hdr.udp.dst_port) {
            MEAS_UDP_PORT: parse_meas;
            HOPVAR_UDP_PORT: parse_hopvar;
            default: accept;
        }
    }

    state parse_tcp {
        packet.extract(hdr.tcp);
        transition accept;
    }

    state parse_meas {
        packet.extract(hdr.meas);
        transition accept;
    }

    state parse_hopvar {
        packet.extract(hdr.hopvar);
        transition accept;
    }
}

// ---------------------------------------------------------------
//     Egress Control
// ---------------------------------------------------------------
control Egress(
    inout header_t hdr,
    inout metadata_t eg_md,
    in egress_intrinsic_metadata_t eg_intr_md,
    in egress_intrinsic_metadata_from_parser_t eg_intr_from_prsr,
    inout egress_intrinsic_metadata_for_deparser_t eg_intr_md_for_dprsr,
    inout egress_intrinsic_metadata_for_output_port_t eg_intr_md_for_oport)
{
    action drop() {
        eg_intr_md_for_dprsr.drop_ctl = 0x1; // Mark packet for dropping
    }

    action meas_egress_timestamp() {
        // T5 = packet enters Tofino egress pipeline
        hdr.meas.ts5 = (bit<64>)eg_intr_from_prsr.global_tstamp;

        // valid_bitmap |= MEAS_V_T5 (bit 4)
        hdr.meas.valid_bitmap = hdr.meas.valid_bitmap | 32w0x00000010;

        // flags |= MEAS_F_SWITCH_TOUCHED (bit 0)
        hdr.meas.flags = hdr.meas.flags | 16w0x0001;

        // simplify UDP checksum update by just setting it to 0 (since we're only adding a timestamp header, this is fine for testing)
        hdr.udp.checksum = 0;
    }
    
    table t_meas_egress_timestamp {
        key = {
            eg_intr_md.egress_port: exact;
            hdr.meas.isValid(): exact;
        }
        actions = {
            meas_egress_timestamp;
            NoAction;
        }
        size = 256;
        default_action = NoAction();
    }

    apply {
        if (hdr.meas.isValid()) {
            t_meas_egress_timestamp.apply();
        }
    }
}

// ---------------------------------------------------------------
//      Egress Deparser
// ---------------------------------------------------------------
control EgressDeparser(packet_out packet,
                        inout header_t hdr,
                        in metadata_t eg_md,
                        in egress_intrinsic_metadata_for_deparser_t eg_intr_dprsr_md)
{
    apply {
        packet.emit(hdr.ethernet);
        packet.emit(hdr.ipv4);
        packet.emit(hdr.udp);
        packet.emit(hdr.tcp);
        packet.emit(hdr.meas);
        packet.emit(hdr.hopvar);
    }
}

// ---------------------------------------------------------------
//          Main Control Block
// ---------------------------------------------------------------
Pipeline(IngressParser(),
        Ingress(),
        IngressDeparser(),
        EgressParser(),
        Egress(),
        EgressDeparser()) pipe;

Switch(pipe) main;
