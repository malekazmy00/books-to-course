def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "real_termbase: reads the licensed termbase releases under B2C_TERMBASE_DIR; skips when absent",
    )
