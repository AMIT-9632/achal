// SPDX-License-Identifier: GPL-2.0-only
// ns-3 cross-check of the Python Wi-Fi link/MAC abstraction (services/hw-sim, option A),
// single-hop variant: every node is in direct range of the gateway (true at the
// guide's 10-20 m spacing, where the Python mesh also builds a one-layer tree).
//
// N ESP32-like nodes on a grid with spacing d send a 64 B UDP reading every
// `interval` seconds to a gateway node (the Raspberry Pi), one hop over ad-hoc
// 802.11 (DCF), OFDM 6 Mb/s (802.11a PHY timing), path loss at 2.437 GHz,
// 17 dBm. Propagation: two-ray ground beyond the breakpoint (TwoRayGround with the
// same antenna heights as the Python model) plus the same fixed clutter loss.
// Output: one CSV line with PDR and delay percentiles from FlowMonitor.
//
// ns-3 has no model of ESP-WIFI-MESH (Espressif's tree protocol), so the
// comparison checks the PHY/MAC abstraction (link reliability, per-hop MAC
// delay, contention), not the routing protocol.
#include "ns3/applications-module.h"
#include "ns3/core-module.h"
#include "ns3/flow-monitor-module.h"
#include "ns3/internet-module.h"
#include "ns3/mobility-module.h"
#include "ns3/network-module.h"
#include "ns3/propagation-module.h"
#include "ns3/wifi-module.h"

#include <algorithm>
#include <cmath>
#include <iostream>
#include <vector>

using namespace ns3;

int main(int argc, char *argv[]) {
    uint32_t n = 20, cols = 0, seed = 1;
    double spacing = 10.0, interval = 1.0, simTime = 120.0, clutterDb = 6.0;
    double nodeH = 1.0, gwH = 3.0;
    CommandLine cmd;
    cmd.AddValue("n", "nodes", n);
    cmd.AddValue("spacing", "grid spacing m", spacing);
    cmd.AddValue("interval", "report interval s", interval);
    cmd.AddValue("time", "simulated seconds", simTime);
    cmd.AddValue("seed", "run number", seed);
    cmd.Parse(argc, argv);
    RngSeedManager::SetSeed(12345);
    RngSeedManager::SetRun(seed);
    if (cols == 0) cols = (uint32_t)std::ceil(std::sqrt(n * 1.25));

    NodeContainer nodes;
    nodes.Create(n + 1);  // last = gateway
    MobilityHelper mob;
    Ptr<ListPositionAllocator> pos = CreateObject<ListPositionAllocator>();
    double ymax = 0;
    for (uint32_t k = 0; k < n; k++) {
        pos->Add(Vector((k % cols) * spacing, (k / cols) * spacing, nodeH));
        ymax = std::max(ymax, (double)(k / cols) * spacing);
    }
    pos->Add(Vector(-10.0, ymax / 2, gwH));
    mob.SetPositionAllocator(pos);
    mob.SetMobilityModel("ns3::ConstantPositionMobilityModel");
    mob.Install(nodes);

    YansWifiChannelHelper ch2;
    ch2.SetPropagationDelay("ns3::ConstantSpeedPropagationDelayModel");
    ch2.AddPropagationLoss("ns3::TwoRayGroundPropagationLossModel", "Frequency", DoubleValue(2.437e9),
                           "HeightAboveZ", DoubleValue(0.0), "MinDistance", DoubleValue(1.0));
    // The Python model's fixed clutter loss is applied as reduced TX power; its distance-proportional
    // excess loss, shadowing and fast fading are switched off for this comparison (see ns3/README.md).
    YansWifiPhyHelper phy;
    phy.SetChannel(ch2.Create());
    phy.Set("TxPowerStart", DoubleValue(17.0 - clutterDb));
    phy.Set("TxPowerEnd", DoubleValue(17.0 - clutterDb));
    phy.Set("RxSensitivity", DoubleValue(-93.0));

    WifiHelper wifi;
    wifi.SetStandard(WIFI_STANDARD_80211a);  // OFDM 6 Mb/s, 9 us slot: same timing as ERP-OFDM at 2.4 GHz
    wifi.SetRemoteStationManager("ns3::ConstantRateWifiManager", "DataMode", StringValue("OfdmRate6Mbps"),
                                 "ControlMode", StringValue("OfdmRate6Mbps"));
    WifiMacHelper mac;
    mac.SetType("ns3::AdhocWifiMac");
    NetDeviceContainer devs = wifi.Install(phy, mac, nodes);

    InternetStackHelper inet;
    inet.Install(nodes);
    Ipv4AddressHelper addr;
    addr.SetBase("10.1.0.0", "255.255.0.0");
    Ipv4InterfaceContainer ifs = addr.Assign(devs);

    uint16_t port = 9;
    PacketSinkHelper sink("ns3::UdpSocketFactory", InetSocketAddress(Ipv4Address::GetAny(), port));
    sink.Install(nodes.Get(n)).Start(Seconds(0.0));
    double warmup = 2.0;  // ARP
    Ptr<UniformRandomVariable> u = CreateObject<UniformRandomVariable>();
    for (uint32_t k = 0; k < n; k++) {
        OnOffHelper app("ns3::UdpSocketFactory", InetSocketAddress(ifs.GetAddress(n), port));
        app.SetConstantRate(DataRate((uint64_t)(64 * 8 / interval)), 64);
        app.SetAttribute("OnTime", StringValue("ns3::ConstantRandomVariable[Constant=1e9]"));
        app.SetAttribute("OffTime", StringValue("ns3::ConstantRandomVariable[Constant=0]"));
        ApplicationContainer a = app.Install(nodes.Get(k));
        a.Start(Seconds(warmup + u->GetValue(0, interval)));
        a.Stop(Seconds(warmup + simTime));
    }
    FlowMonitorHelper fmh;
    Ptr<FlowMonitor> fm = fmh.InstallAll();
    Simulator::Stop(Seconds(warmup + simTime + 5.0));
    Simulator::Run();
    fm->CheckForLostPackets();
    uint64_t tx = 0, rx = 0;
    std::vector<double> delays;
    for (auto &kv : fm->GetFlowStats()) {
        tx += kv.second.txPackets;
        rx += kv.second.rxPackets;
        if (kv.second.rxPackets) delays.push_back(kv.second.delaySum.GetSeconds() / kv.second.rxPackets);
    }
    std::sort(delays.begin(), delays.end());
    double med = delays.empty() ? NAN : delays[delays.size() / 2];
    double p95 = delays.empty() ? NAN : delays[(size_t)(0.95 * (delays.size() - 1))];
    std::cout << "n,spacing,interval,seed,tx,rx,pdr,flow_mean_delay_ms_median,flow_mean_delay_ms_p95\n"
              << n << "," << spacing << "," << interval << "," << seed << "," << tx << "," << rx << ","
              << (tx ? (double)rx / tx : NAN) << "," << med * 1000 << "," << p95 * 1000 << "\n";
    Simulator::Destroy();
    return 0;
}
