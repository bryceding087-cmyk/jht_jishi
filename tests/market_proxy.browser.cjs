// Reuse the real-browser journey through TLS termination and Host rewriting.
process.env.JHT_TEST_PROXY = '1';
require('./market_login.browser.cjs').run().catch(error=>{console.error(error);process.exitCode=1;});
