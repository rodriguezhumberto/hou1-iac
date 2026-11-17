# Lab container DNS

Owns the lab DNS firewall policy, reconciler and its marked UFW rules. The Docker
role invokes it after runtime installation; Compose activation invokes it again
after network creation and before service startup. Inputs declare network names;
rules use their actual bridge interfaces and subnets. Internal networks are skipped.
See [ownership and removal](../../../../docs/lab-dns.md).
