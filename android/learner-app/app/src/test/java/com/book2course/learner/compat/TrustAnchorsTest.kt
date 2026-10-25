package com.book2course.learner.compat

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test
import java.io.File
import java.security.KeyStore
import java.security.MessageDigest
import java.security.cert.CertPathValidator
import java.security.cert.CertPathValidatorException
import java.security.cert.CertificateFactory
import java.security.cert.PKIXParameters
import java.security.cert.TrustAnchor
import java.security.cert.X509Certificate
import java.time.Instant
import java.util.Date

/**
 * Android 7.0 (API 24) has neither ISRG Root X1 (added in 7.1.1) nor X2 (added in 14) in its
 * system store, and the DST Root CA X3 cross-sign that bridged it expired on 2024-09-30. The app
 * therefore ships both ISRG roots as extra trust anchors (res/raw, network_security_config).
 *
 * These checks run on the JVM: they validate the chain Tailscale Serve actually sent on
 * 2026-10-25 (leaf <- YE1 <- Root YE <- ISRG Root X2 cross-signed by X1) against the bundled files.
 */
class TrustAnchorsTest {
    private val factory = CertificateFactory.getInstance("X.509")
    private val raw = File("src/main/res/raw")

    private fun load(file: File): List<X509Certificate> =
        file.inputStream().use { input -> factory.generateCertificates(input).map { it as X509Certificate } }

    private fun sha256(cert: X509Certificate): String =
        MessageDigest.getInstance("SHA-256").digest(cert.encoded).joinToString(":") { "%02X".format(it) }

    private val servedChain = load(File(javaClass.getResource("/tls/tailscale-serve-chain-2026-10-25.pem")!!.toURI()))

    /**
     * Like Android's chain builder, stop at the first served certificate whose issuer is an anchor
     * (with X2 as anchor, the served "X2 cross-signed by X1" is not needed).
     */
    private fun validate(anchors: Set<TrustAnchor>) {
        val anchorNames = anchors.map { it.trustedCert.subjectX500Principal }.toSet()
        val end = servedChain.indexOfFirst { it.issuerX500Principal in anchorNames }
        val path = if (end >= 0) servedChain.subList(0, end + 1) else servedChain
        val params = PKIXParameters(anchors).apply {
            isRevocationEnabled = false
            date = Date.from(Instant.parse("2026-10-25T12:00:00Z")) // the leaf is valid 2026-10-05 .. 2027-01-03
        }
        CertPathValidator.getInstance("PKIX").validate(factory.generateCertPath(path), params)
    }

    @Test
    fun `bundled roots are exactly the ones Let's Encrypt and Mozilla CCADB publish`() {
        val x1 = load(File(raw, "isrg_root_x1.pem")).single()
        val x2 = load(File(raw, "isrg_root_x2.pem")).single()
        assertEquals("CN=ISRG Root X1,O=Internet Security Research Group,C=US", x1.subjectX500Principal.name)
        assertEquals(x1.subjectX500Principal, x1.issuerX500Principal) // self-signed
        assertEquals("96:BC:EC:06:26:49:76:F3:74:60:77:9A:CF:28:C5:A7:CF:E8:A3:C0:AA:E1:1A:8F:FC:EE:05:C0:BD:DF:08:C6", sha256(x1))
        assertEquals("CN=ISRG Root X2,O=Internet Security Research Group,C=US", x2.subjectX500Principal.name)
        assertEquals(x2.subjectX500Principal, x2.issuerX500Principal)
        assertEquals("69:72:9B:8E:15:A8:6E:FC:17:7A:57:AF:B7:17:1D:FC:64:AD:D2:8C:2F:CA:8C:F1:50:7E:34:45:3C:CB:14:70", sha256(x2))
    }

    @Test
    fun `served Tailscale chain validates against each bundled root`() {
        validate(setOf(TrustAnchor(load(File(raw, "isrg_root_x1.pem")).single(), null)))
        validate(setOf(TrustAnchor(load(File(raw, "isrg_root_x2.pem")).single(), null)))
    }

    @Test
    fun `served chain fails on a store without ISRG roots, as on Android 7_0`() {
        val jdk = KeyStore.getInstance(KeyStore.getDefaultType()).apply {
            File(System.getProperty("java.home"), "lib/security/cacerts").inputStream().use { load(it, null) }
        }
        val withoutIsrg = jdk.aliases().toList()
            .mapNotNull { jdk.getCertificate(it) as? X509Certificate }
            .filterNot { it.subjectX500Principal.name.contains("ISRG") }
            .map { TrustAnchor(it, null) }.toSet()
        assertThrows(CertPathValidatorException::class.java) { validate(withoutIsrg) }
    }
}
