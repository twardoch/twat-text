"""Test suite for twat_text."""
import pytest
from unittest.mock import Mock, patch

import twat_text
from twat_text.twat_text import Config, process_data, main


def test_version():
    """Verify package exposes version."""
    assert twat_text.__version__


class TestConfig:
    """Test cases for Config dataclass."""
    
    def test_config_creation_with_required_fields(self):
        """Test Config creation with required fields."""
        config = Config(name="test", value="test_value")
        assert config.name == "test"
        assert config.value == "test_value"
        assert config.options is None
    
    def test_config_creation_with_all_fields(self):
        """Test Config creation with all fields."""
        options = {"key1": "value1", "key2": "value2"}
        config = Config(name="test", value=42, options=options)
        assert config.name == "test"
        assert config.value == 42
        assert config.options == options
    
    def test_config_with_different_value_types(self):
        """Test Config with different value types."""
        # String value
        config_str = Config(name="test_str", value="string_value")
        assert isinstance(config_str.value, str)
        
        # Integer value
        config_int = Config(name="test_int", value=123)
        assert isinstance(config_int.value, int)
        
        # Float value
        config_float = Config(name="test_float", value=3.14)
        assert isinstance(config_float.value, float)


class TestProcessData:
    """Test cases for process_data function."""
    
    def test_process_data_with_empty_list_raises_error(self):
        """Test that empty data list raises ValueError."""
        with pytest.raises(ValueError, match="Input data cannot be empty"):
            process_data([])
    
    def test_process_data_with_valid_data(self):
        """Test process_data with valid data."""
        data = ["test", "data"]
        result = process_data(data)
        assert isinstance(result, dict)
        assert result == {}  # Currently returns empty dict
    
    def test_process_data_with_config(self):
        """Test process_data with configuration."""
        data = ["test", "data"]
        config = Config(name="test_config", value="test_value")
        result = process_data(data, config=config)
        assert isinstance(result, dict)
        assert result == {}  # Currently returns empty dict
    
    def test_process_data_with_debug_mode(self):
        """Test process_data with debug mode enabled."""
        data = ["test", "data"]
        with patch('twat_text.twat_text.logger') as mock_logger:
            result = process_data(data, debug=True)
            mock_logger.setLevel.assert_called_once()
            mock_logger.debug.assert_called_once_with("Debug mode enabled")
            assert isinstance(result, dict)
    
    def test_process_data_with_various_data_types(self):
        """Test process_data with various data types."""
        data = ["string", 123, 3.14, {"key": "value"}, [1, 2, 3]]
        result = process_data(data)
        assert isinstance(result, dict)
        assert result == {}  # Currently returns empty dict
    
    def test_process_data_with_none_config(self):
        """Test process_data with None config."""
        data = ["test", "data"]
        result = process_data(data, config=None)
        assert isinstance(result, dict)
        assert result == {}  # Currently returns empty dict


class TestMain:
    """Test cases for main function."""
    
    def test_main_function_runs_successfully(self):
        """Test that main function runs without errors."""
        with patch('sys.argv', ['twat-text']):
            with patch('twat_text.twat_text.logger') as mock_logger:
                # This should not raise an exception
                result = main()
                # Verify logger was called and function returns success
                mock_logger.info.assert_called_once()
                assert result == 0
    
    def test_main_function_handles_exceptions(self):
        """Test main function exception handling."""
        with patch('sys.argv', ['twat-text']):
            with patch('twat_text.twat_text.process_data') as mock_process_data:
                mock_process_data.side_effect = Exception("Test exception")
                result = main()
                assert result == 1
    
    def test_main_function_with_args(self):
        """Test main function with command line arguments."""
        with patch('sys.argv', ['twat-text', '--debug', '--config', 'name=test', 'data1', 'data2']):
            with patch('twat_text.twat_text.logger') as mock_logger:
                result = main()
                assert mock_logger.setLevel.call_count >= 1  # Called at least once
                mock_logger.debug.assert_called_with("Debug mode enabled")
                assert result == 0


class TestIntegration:
    """Integration tests for the module."""
    
    def test_complete_workflow(self):
        """Test complete workflow with realistic data."""
        # Create a config
        config = Config(
            name="integration_test",
            value="test_value",
            options={"debug": True, "mode": "test"}
        )
        
        # Test data
        test_data = ["sample", "text", "data"]
        
        # Process data
        result = process_data(test_data, config=config, debug=True)
        
        # Verify result
        assert isinstance(result, dict)
        # Note: Since process_data is currently a placeholder, we're testing the interface
    
    def test_error_handling_integration(self):
        """Test error handling in integration scenario."""
        # Test with invalid data
        with pytest.raises(ValueError):
            process_data([], config=None, debug=False)
        
        # Test with valid data should work
        result = process_data(["valid", "data"])
        assert isinstance(result, dict)


# Performance/Benchmark tests (if pytest-benchmark is available)
class TestPerformance:
    """Performance tests for the module."""
    
    def test_process_data_performance(self, benchmark):
        """Benchmark process_data function."""
        data = ["test"] * 1000  # Large dataset
        config = Config(name="perf_test", value="test")
        
        result = benchmark(process_data, data, config=config)
        assert isinstance(result, dict)
    
    def test_config_creation_performance(self, benchmark):
        """Benchmark Config creation."""
        options = {f"key_{i}": f"value_{i}" for i in range(100)}
        
        def create_config():
            return Config(name="perf_test", value="test", options=options)
        
        config = benchmark(create_config)
        assert isinstance(config, Config)
        assert len(config.options) == 100
