package engine

import (
	"context"
	"crypto/rand"
	"crypto/rsa"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"math/big"
	"net"
	"os"
	"path/filepath"
	"testing"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials"

	"undolog-proxy/internal/engine/pb"
	"undolog-proxy/internal/protocol"
)

// testEngineServer provides a minimal stub implementing pb.UndoLogEngineServer
// for transport security testing.
type testEngineServer struct {
	pb.UnimplementedUndoLogEngineServer
}

func (s *testEngineServer) Intercept(context.Context, *pb.InterceptRequest) (*pb.InterceptResponse, error) {
	return &pb.InterceptResponse{Outcome: &pb.InterceptResponse_Execute{Execute: &pb.ExecuteOutcome{}}}, nil
}

func (s *testEngineServer) Commit(context.Context, *pb.CommitRequest) (*pb.CommitResponse, error) {
	return &pb.CommitResponse{}, nil
}

func (s *testEngineServer) Fail(context.Context, *pb.FailRequest) (*pb.FailResponse, error) {
	return &pb.FailResponse{}, nil
}

func (s *testEngineServer) Approve(context.Context, *pb.ApproveRequest) (*pb.ApproveResponse, error) {
	return &pb.ApproveResponse{EffectId: "eff-1"}, nil
}

func (s *testEngineServer) Reject(context.Context, *pb.RejectRequest) (*pb.RejectResponse, error) {
	return &pb.RejectResponse{}, nil
}

func (s *testEngineServer) ListPendingApprovals(context.Context, *pb.ListPendingApprovalsRequest) (*pb.ListPendingApprovalsResponse, error) {
	return &pb.ListPendingApprovalsResponse{}, nil
}

func (s *testEngineServer) GetApproval(context.Context, *pb.GetApprovalRequest) (*pb.GetApprovalResponse, error) {
	return &pb.GetApprovalResponse{}, nil
}

func (s *testEngineServer) ListDeadLetters(context.Context, *pb.ListDeadLettersRequest) (*pb.ListDeadLettersResponse, error) {
	return &pb.ListDeadLettersResponse{}, nil
}

func (s *testEngineServer) RetryDeadLetter(context.Context, *pb.RetryDeadLetterRequest) (*pb.RetryDeadLetterResponse, error) {
	return &pb.RetryDeadLetterResponse{}, nil
}

func (s *testEngineServer) SkipDeadLetter(context.Context, *pb.SkipDeadLetterRequest) (*pb.SkipDeadLetterResponse, error) {
	return &pb.SkipDeadLetterResponse{}, nil
}

type testCerts struct {
	caPath              string
	serverCertPath      string
	serverKeyPath       string
	clientCertPath      string
	clientKeyPath       string
	untrustedCAPath     string
	untrustedClientCert string
	untrustedClientKey  string
	corruptPath         string
}

func generateTestCerts(t *testing.T) testCerts {
	t.Helper()
	dir := t.TempDir()

	caKey, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatalf("generate CA key: %v", err)
	}
	caTemplate := &x509.Certificate{
		SerialNumber:          big.NewInt(1),
		Subject:               pkix.Name{CommonName: "Test CA"},
		NotBefore:             time.Now().Add(-time.Hour),
		NotAfter:              time.Now().Add(24 * time.Hour),
		IsCA:                  true,
		KeyUsage:              x509.KeyUsageCertSign | x509.KeyUsageCRLSign,
		BasicConstraintsValid: true,
	}
	caDER, err := x509.CreateCertificate(rand.Reader, caTemplate, caTemplate, &caKey.PublicKey, caKey)
	if err != nil {
		t.Fatalf("create CA cert: %v", err)
	}
	caPath := filepath.Join(dir, "ca.crt")
	writePEM(t, caPath, "CERTIFICATE", caDER)

	serverKey, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatalf("generate server key: %v", err)
	}
	serverTemplate := &x509.Certificate{
		SerialNumber: big.NewInt(2),
		Subject:      pkix.Name{CommonName: "localhost"},
		NotBefore:    time.Now().Add(-time.Hour),
		NotAfter:     time.Now().Add(24 * time.Hour),
		IPAddresses:  []net.IP{net.ParseIP("127.0.0.1")},
		DNSNames:     []string{"localhost"},
		KeyUsage:     x509.KeyUsageDigitalSignature | x509.KeyUsageKeyEncipherment,
		ExtKeyUsage:  []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
	}
	serverDER, err := x509.CreateCertificate(rand.Reader, serverTemplate, caTemplate, &serverKey.PublicKey, caKey)
	if err != nil {
		t.Fatalf("create server cert: %v", err)
	}
	serverCertPath := filepath.Join(dir, "server.crt")
	serverKeyPath := filepath.Join(dir, "server.key")
	writePEM(t, serverCertPath, "CERTIFICATE", serverDER)
	writePEM(t, serverKeyPath, "RSA PRIVATE KEY", x509.MarshalPKCS1PrivateKey(serverKey))

	clientKey, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatalf("generate client key: %v", err)
	}
	clientTemplate := &x509.Certificate{
		SerialNumber: big.NewInt(3),
		Subject:      pkix.Name{CommonName: "test-client"},
		NotBefore:    time.Now().Add(-time.Hour),
		NotAfter:     time.Now().Add(24 * time.Hour),
		KeyUsage:     x509.KeyUsageDigitalSignature,
		ExtKeyUsage:  []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth},
	}
	clientDER, err := x509.CreateCertificate(rand.Reader, clientTemplate, caTemplate, &clientKey.PublicKey, caKey)
	if err != nil {
		t.Fatalf("create client cert: %v", err)
	}
	clientCertPath := filepath.Join(dir, "client.crt")
	clientKeyPath := filepath.Join(dir, "client.key")
	writePEM(t, clientCertPath, "CERTIFICATE", clientDER)
	writePEM(t, clientKeyPath, "RSA PRIVATE KEY", x509.MarshalPKCS1PrivateKey(clientKey))

	untrustedCAKey, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatalf("generate untrusted CA key: %v", err)
	}
	untrustedCATemplate := &x509.Certificate{
		SerialNumber:          big.NewInt(4),
		Subject:               pkix.Name{CommonName: "Untrusted CA"},
		NotBefore:             time.Now().Add(-time.Hour),
		NotAfter:              time.Now().Add(24 * time.Hour),
		IsCA:                  true,
		KeyUsage:              x509.KeyUsageCertSign | x509.KeyUsageCRLSign,
		BasicConstraintsValid: true,
	}
	untrustedCADER, err := x509.CreateCertificate(rand.Reader, untrustedCATemplate, untrustedCATemplate, &untrustedCAKey.PublicKey, untrustedCAKey)
	if err != nil {
		t.Fatalf("create untrusted CA cert: %v", err)
	}
	untrustedCAPath := filepath.Join(dir, "untrusted_ca.crt")
	writePEM(t, untrustedCAPath, "CERTIFICATE", untrustedCADER)

	untrustedClientKey, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatalf("generate untrusted client key: %v", err)
	}
	untrustedClientTemplate := &x509.Certificate{
		SerialNumber: big.NewInt(5),
		Subject:      pkix.Name{CommonName: "untrusted-client"},
		NotBefore:    time.Now().Add(-time.Hour),
		NotAfter:     time.Now().Add(24 * time.Hour),
		KeyUsage:     x509.KeyUsageDigitalSignature,
		ExtKeyUsage:  []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth},
	}
	untrustedClientDER, err := x509.CreateCertificate(rand.Reader, untrustedClientTemplate, untrustedCATemplate, &untrustedClientKey.PublicKey, untrustedCAKey)
	if err != nil {
		t.Fatalf("create untrusted client cert: %v", err)
	}
	untrustedClientCert := filepath.Join(dir, "untrusted_client.crt")
	untrustedClientKeyPath := filepath.Join(dir, "untrusted_client.key")
	writePEM(t, untrustedClientCert, "CERTIFICATE", untrustedClientDER)
	writePEM(t, untrustedClientKeyPath, "RSA PRIVATE KEY", x509.MarshalPKCS1PrivateKey(untrustedClientKey))

	corruptPath := filepath.Join(dir, "corrupt.crt")
	if err := os.WriteFile(corruptPath, []byte("NOT A VALID PEM CERTIFICATE"), 0o600); err != nil {
		t.Fatalf("write corrupt cert: %v", err)
	}

	return testCerts{
		caPath:              caPath,
		serverCertPath:      serverCertPath,
		serverKeyPath:       serverKeyPath,
		clientCertPath:      clientCertPath,
		clientKeyPath:       clientKeyPath,
		untrustedCAPath:     untrustedCAPath,
		untrustedClientCert: untrustedClientCert,
		untrustedClientKey:  untrustedClientKeyPath,
		corruptPath:         corruptPath,
	}
}

func writePEM(t *testing.T, path, blockType string, data []byte) {
	t.Helper()
	f, err := os.Create(path)
	if err != nil {
		t.Fatalf("create PEM file %s: %v", path, err)
	}
	defer func() { _ = f.Close() }()
	if err := pem.Encode(f, &pem.Block{Type: blockType, Bytes: data}); err != nil {
		t.Fatalf("encode PEM block: %v", err)
	}
}

func TestPlaintextConnection(t *testing.T) {
	lis, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	srv := grpc.NewServer()
	pb.RegisterUndoLogEngineServer(srv, &testEngineServer{})
	go func() { _ = srv.Serve(lis) }()
	defer srv.Stop()

	creds, err := BuildTransportCredentials(TLSConfig{})
	if err != nil {
		t.Fatalf("build credentials: %v", err)
	}

	conn, err := grpc.NewClient(lis.Addr().String(), grpc.WithTransportCredentials(creds))
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	defer func() { _ = conn.Close() }()

	transport := NewGRPCTransport(conn)
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()

	err = transport.Commit(ctx, protocol.CommitRequest{EffectID: "eff-1"})
	if err != nil {
		t.Fatalf("commit failed over plaintext: %v", err)
	}
}

func TestTLSConnectionWithValidCA(t *testing.T) {
	certs := generateTestCerts(t)

	serverPair, err := tls.LoadX509KeyPair(certs.serverCertPath, certs.serverKeyPath)
	if err != nil {
		t.Fatalf("load server key pair: %v", err)
	}
	serverTLS := credentials.NewTLS(&tls.Config{
		Certificates: []tls.Certificate{serverPair},
	})

	lis, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	srv := grpc.NewServer(grpc.Creds(serverTLS))
	pb.RegisterUndoLogEngineServer(srv, &testEngineServer{})
	go func() { _ = srv.Serve(lis) }()
	defer srv.Stop()

	creds, err := BuildTransportCredentials(TLSConfig{
		CAPath: certs.caPath,
	})
	if err != nil {
		t.Fatalf("build credentials: %v", err)
	}

	conn, err := grpc.NewClient(lis.Addr().String(), grpc.WithTransportCredentials(creds))
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	defer func() { _ = conn.Close() }()

	transport := NewGRPCTransport(conn)
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()

	err = transport.Commit(ctx, protocol.CommitRequest{EffectID: "eff-1"})
	if err != nil {
		t.Fatalf("commit failed over TLS: %v", err)
	}
}

func TestMTLSConnectionWithClientCert(t *testing.T) {
	certs := generateTestCerts(t)

	serverPair, err := tls.LoadX509KeyPair(certs.serverCertPath, certs.serverKeyPath)
	if err != nil {
		t.Fatalf("load server key pair: %v", err)
	}
	caPEM, err := os.ReadFile(certs.caPath)
	if err != nil {
		t.Fatalf("read CA: %v", err)
	}
	caPool := x509.NewCertPool()
	caPool.AppendCertsFromPEM(caPEM)

	serverTLS := credentials.NewTLS(&tls.Config{
		Certificates: []tls.Certificate{serverPair},
		ClientAuth:   tls.RequireAndVerifyClientCert,
		ClientCAs:    caPool,
	})

	lis, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	srv := grpc.NewServer(grpc.Creds(serverTLS))
	pb.RegisterUndoLogEngineServer(srv, &testEngineServer{})
	go func() { _ = srv.Serve(lis) }()
	defer srv.Stop()

	creds, err := BuildTransportCredentials(TLSConfig{
		CAPath:         certs.caPath,
		ClientCertPath: certs.clientCertPath,
		ClientKeyPath:  certs.clientKeyPath,
	})
	if err != nil {
		t.Fatalf("build credentials: %v", err)
	}

	conn, err := grpc.NewClient(lis.Addr().String(), grpc.WithTransportCredentials(creds))
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	defer func() { _ = conn.Close() }()

	transport := NewGRPCTransport(conn)
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()

	err = transport.Commit(ctx, protocol.CommitRequest{EffectID: "eff-1"})
	if err != nil {
		t.Fatalf("commit failed over mTLS: %v", err)
	}
}

func TestHandshakeFailsWithUntrustedServer(t *testing.T) {
	certs := generateTestCerts(t)

	serverPair, err := tls.LoadX509KeyPair(certs.serverCertPath, certs.serverKeyPath)
	if err != nil {
		t.Fatalf("load server key pair: %v", err)
	}
	serverTLS := credentials.NewTLS(&tls.Config{
		Certificates: []tls.Certificate{serverPair},
	})

	lis, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	srv := grpc.NewServer(grpc.Creds(serverTLS))
	pb.RegisterUndoLogEngineServer(srv, &testEngineServer{})
	go func() { _ = srv.Serve(lis) }()
	defer srv.Stop()

	// Client trusts only the untrusted CA, so the server's certificate is rejected.
	creds, err := BuildTransportCredentials(TLSConfig{
		CAPath: certs.untrustedCAPath,
	})
	if err != nil {
		t.Fatalf("build credentials: %v", err)
	}

	conn, err := grpc.NewClient(lis.Addr().String(), grpc.WithTransportCredentials(creds))
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	defer func() { _ = conn.Close() }()

	transport := NewGRPCTransport(conn)
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()

	err = transport.Commit(ctx, protocol.CommitRequest{EffectID: "eff-1"})
	if err == nil {
		t.Fatal("expected handshake failure when server certificate is untrusted")
	}
}

func TestMTLSRejectedWhenClientCertMissing(t *testing.T) {
	certs := generateTestCerts(t)

	serverPair, err := tls.LoadX509KeyPair(certs.serverCertPath, certs.serverKeyPath)
	if err != nil {
		t.Fatalf("load server key pair: %v", err)
	}
	caPEM, err := os.ReadFile(certs.caPath)
	if err != nil {
		t.Fatalf("read CA: %v", err)
	}
	caPool := x509.NewCertPool()
	caPool.AppendCertsFromPEM(caPEM)

	serverTLS := credentials.NewTLS(&tls.Config{
		Certificates: []tls.Certificate{serverPair},
		ClientAuth:   tls.RequireAndVerifyClientCert,
		ClientCAs:    caPool,
	})

	lis, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	srv := grpc.NewServer(grpc.Creds(serverTLS))
	pb.RegisterUndoLogEngineServer(srv, &testEngineServer{})
	go func() { _ = srv.Serve(lis) }()
	defer srv.Stop()

	// Client has trusted CA but supplies NO client certificate.
	creds, err := BuildTransportCredentials(TLSConfig{
		CAPath: certs.caPath,
	})
	if err != nil {
		t.Fatalf("build credentials: %v", err)
	}

	conn, err := grpc.NewClient(lis.Addr().String(), grpc.WithTransportCredentials(creds))
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	defer func() { _ = conn.Close() }()

	transport := NewGRPCTransport(conn)
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()

	err = transport.Commit(ctx, protocol.CommitRequest{EffectID: "eff-1"})
	if err == nil {
		t.Fatal("expected mTLS handshake failure when client certificate is missing")
	}
}

func TestMTLSRejectedWhenClientCertUntrusted(t *testing.T) {
	certs := generateTestCerts(t)

	serverPair, err := tls.LoadX509KeyPair(certs.serverCertPath, certs.serverKeyPath)
	if err != nil {
		t.Fatalf("load server key pair: %v", err)
	}
	caPEM, err := os.ReadFile(certs.caPath)
	if err != nil {
		t.Fatalf("read CA: %v", err)
	}
	caPool := x509.NewCertPool()
	caPool.AppendCertsFromPEM(caPEM)

	serverTLS := credentials.NewTLS(&tls.Config{
		Certificates: []tls.Certificate{serverPair},
		ClientAuth:   tls.RequireAndVerifyClientCert,
		ClientCAs:    caPool,
	})

	lis, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	srv := grpc.NewServer(grpc.Creds(serverTLS))
	pb.RegisterUndoLogEngineServer(srv, &testEngineServer{})
	go func() { _ = srv.Serve(lis) }()
	defer srv.Stop()

	// Client presents client certificate signed by untrusted CA.
	creds, err := BuildTransportCredentials(TLSConfig{
		CAPath:         certs.caPath,
		ClientCertPath: certs.untrustedClientCert,
		ClientKeyPath:  certs.untrustedClientKey,
	})
	if err != nil {
		t.Fatalf("build credentials: %v", err)
	}

	conn, err := grpc.NewClient(lis.Addr().String(), grpc.WithTransportCredentials(creds))
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	defer func() { _ = conn.Close() }()

	transport := NewGRPCTransport(conn)
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()

	err = transport.Commit(ctx, protocol.CommitRequest{EffectID: "eff-1"})
	if err == nil {
		t.Fatal("expected mTLS handshake failure when client certificate is untrusted")
	}
}

func TestStartupFailsWhenFilesUnreadable(t *testing.T) {
	certs := generateTestCerts(t)

	t.Run("missing CA file fails", func(t *testing.T) {
		_, err := BuildTransportCredentials(TLSConfig{
			CAPath: filepath.Join(t.TempDir(), "nonexistent.crt"),
		})
		if err == nil {
			t.Fatal("expected error for missing CA file")
		}
	})

	t.Run("missing client cert file fails", func(t *testing.T) {
		_, err := BuildTransportCredentials(TLSConfig{
			CAPath:         certs.caPath,
			ClientCertPath: filepath.Join(t.TempDir(), "nonexistent.crt"),
			ClientKeyPath:  certs.clientKeyPath,
		})
		if err == nil {
			t.Fatal("expected error for missing client cert file")
		}
	})

	t.Run("missing client key file fails", func(t *testing.T) {
		_, err := BuildTransportCredentials(TLSConfig{
			CAPath:         certs.caPath,
			ClientCertPath: certs.clientCertPath,
			ClientKeyPath:  filepath.Join(t.TempDir(), "nonexistent.key"),
		})
		if err == nil {
			t.Fatal("expected error for missing client key file")
		}
	})

	t.Run("corrupt CA file fails", func(t *testing.T) {
		_, err := BuildTransportCredentials(TLSConfig{
			CAPath: certs.corruptPath,
		})
		if err == nil {
			t.Fatal("expected error for corrupt CA file")
		}
	})

	t.Run("client cert without key fails", func(t *testing.T) {
		_, err := BuildTransportCredentials(TLSConfig{
			CAPath:         certs.caPath,
			ClientCertPath: certs.clientCertPath,
		})
		if err == nil {
			t.Fatal("expected error when client cert specified without key")
		}
	})

	t.Run("client key without cert fails", func(t *testing.T) {
		_, err := BuildTransportCredentials(TLSConfig{
			CAPath:        certs.caPath,
			ClientKeyPath: certs.clientKeyPath,
		})
		if err == nil {
			t.Fatal("expected error when client key specified without cert")
		}
	})
}
