package openpayments

import (
	"context"
	"errors"
	"net"
	"net/http"
	"net/netip"
	"time"

	"github.com/sebastian-quintero-osorio/trust-open-payments/open-payments/internal/config"
)

var deniedPrefixes = []netip.Prefix{
	netip.MustParsePrefix("0.0.0.0/8"), netip.MustParsePrefix("100.64.0.0/10"), netip.MustParsePrefix("192.0.0.0/24"), netip.MustParsePrefix("192.0.2.0/24"), netip.MustParsePrefix("198.18.0.0/15"), netip.MustParsePrefix("198.51.100.0/24"), netip.MustParsePrefix("203.0.113.0/24"), netip.MustParsePrefix("240.0.0.0/4"), netip.MustParsePrefix("2001:db8::/32"), netip.MustParsePrefix("64:ff9b::/96"), netip.MustParsePrefix("2002::/16"), netip.MustParsePrefix("2001::/32"),
}

// PublicIP rejects private, special, mapped-private and transition-network targets.
func PublicIP(ip netip.Addr) bool {
	ip = ip.Unmap()
	if !ip.IsGlobalUnicast() || ip.IsPrivate() || ip.IsLoopback() || ip.IsLinkLocalUnicast() {
		return false
	}
	for _, prefix := range deniedPrefixes {
		if prefix.Contains(ip) {
			return false
		}
	}
	return true
}

// SafeHTTPClient pins validated DNS answers at dial time to resist DNS rebinding.
// Environment proxies and redirects are intentionally disabled for signed requests.
func SafeHTTPClient() *http.Client {
	dialer := &net.Dialer{Timeout: 5 * time.Second, KeepAlive: 30 * time.Second}
	transport := &http.Transport{Proxy: nil, MaxResponseHeaderBytes: 32 << 10, ResponseHeaderTimeout: 10 * time.Second, TLSHandshakeTimeout: 5 * time.Second, IdleConnTimeout: 60 * time.Second, MaxIdleConns: 32, DialContext: func(ctx context.Context, network, address string) (net.Conn, error) {
		host, port, e := net.SplitHostPort(address)
		if e != nil {
			return nil, errors.New("invalid outbound host")
		}
		ips, e := net.DefaultResolver.LookupNetIP(ctx, "ip", host)
		if e != nil || len(ips) == 0 {
			return nil, errors.New("outbound DNS lookup failed")
		}
		for _, ip := range ips {
			if !PublicIP(ip) {
				return nil, errors.New("outbound address is not public")
			}
		}
		var last error
		for _, ip := range ips {
			conn, e := dialer.DialContext(ctx, network, net.JoinHostPort(ip.String(), port))
			if e == nil {
				return conn, nil
			}
			last = e
		}
		return nil, last
	}}
	return &http.Client{Transport: transport, Timeout: 15 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
}
func validURL(value string) error {
	if !config.HTTPSURL(value) {
		return &Error{Code: "invalid_url", Status: 422}
	}
	return nil
}
