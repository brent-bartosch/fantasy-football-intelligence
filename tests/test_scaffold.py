def test_package_imports():
    import ffi

    assert ffi.__version__ == "0.1.0"
