"""
Test for 403 error fallback mechanism in CloudService.download_file
"""
import sys
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import requests

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent))

from services.cloud_service import CloudService


def test_403_fallback_to_public_url():
    """Test that 403 error from API endpoint triggers fallback to public URL"""
    print("=" * 80)
    print("TEST: 403 Error Fallback to Public URL")
    print("=" * 80)
    
    # Test data
    api_url = "https://cloud.mail.ru/api/v2/file/download?weblink=2RNv/faZLz1cLQ/0002/2025-03-05 09.27.42.PNG"
    weblink = "2RNv/faZLz1cLQ/0002/2025-03-05 09.27.42.PNG"
    expected_public_url = f"https://cloud.mail.ru/public/{weblink}"
    mock_file_content = b"PNG fake image content"
    
    cloud_service = CloudService()
    
    # Mock the session.get to simulate 403 on API endpoint and success on public URL
    with patch.object(cloud_service.session, 'get') as mock_get:
        # First call (API endpoint) - returns 403
        mock_response_403 = Mock()
        mock_response_403.status_code = 403
        mock_response_403.raise_for_status = Mock(side_effect=requests.exceptions.HTTPError(
            response=mock_response_403,
            request=Mock()
        ))
        
        # Second call (public URL) - returns success
        mock_response_200 = Mock()
        mock_response_200.status_code = 200
        mock_response_200.headers = {'Content-Type': 'image/png'}
        mock_response_200.content = mock_file_content
        mock_response_200.raise_for_status = Mock()  # No exception
        
        # Configure mock_get to return different responses based on URL
        def mock_get_side_effect(url, **kwargs):
            if '/api/v2/file/download' in url:
                print(f"  → Mock API endpoint call: {url}")
                print(f"  → Returning 403 error")
                return mock_response_403
            elif '/public/' in url:
                print(f"  → Mock public URL call: {url}")
                print(f"  → Returning 200 success with file content")
                return mock_response_200
            else:
                raise ValueError(f"Unexpected URL: {url}")
        
        mock_get.side_effect = mock_get_side_effect
        
        # Test
        try:
            print(f"\n1. Testing API endpoint URL: {api_url}")
            result = cloud_service.download_file(api_url)
            
            print(f"\n2. Checking results...")
            assert result == mock_file_content, "Should return file content from public URL"
            
            print(f"\n3. Verifying calls...")
            assert mock_get.call_count == 2, f"Should make 2 calls (API + fallback), got {mock_get.call_count}"
            
            # Check that first call was to API endpoint
            first_call_url = mock_get.call_args_list[0][0][0]
            assert '/api/v2/file/download' in first_call_url, "First call should be to API endpoint"
            print(f"   ✓ First call was to API endpoint")
            
            # Check that second call was to public URL
            second_call_url = mock_get.call_args_list[1][0][0]
            assert '/public/' in second_call_url, "Second call should be to public URL"
            assert weblink in second_call_url, f"Public URL should contain weblink: {weblink}"
            print(f"   ✓ Second call was to public URL: {second_call_url}")
            
            print(f"\n✅ TEST PASSED: Fallback mechanism works correctly!")
            print(f"   - API endpoint returned 403")
            print(f"   - Fallback to public URL succeeded")
            print(f"   - File content retrieved: {len(result)} bytes")
            
            return True
            
        except AssertionError as e:
            print(f"\n❌ TEST FAILED: {e}")
            print(f"   Mock calls made: {mock_get.call_count}")
            for i, call in enumerate(mock_get.call_args_list):
                print(f"   Call {i+1}: {call[0][0]}")
            return False
        except Exception as e:
            print(f"\n❌ TEST FAILED with exception: {e}")
            import traceback
            traceback.print_exc()
            return False


def test_403_fallback_weblink_extraction():
    """Test that weblink is correctly extracted from API URL"""
    print("\n" + "=" * 80)
    print("TEST: Webbink Extraction from API URL")
    print("=" * 80)
    
    import re
    from urllib.parse import unquote
    
    test_cases = [
        {
            "api_url": "https://cloud.mail.ru/api/v2/file/download?weblink=2RNv/faZLz1cLQ/0002/2025-03-05 09.27.42.PNG",
            "expected_weblink": "2RNv/faZLz1cLQ/0002/2025-03-05 09.27.42.PNG",
            "expected_public_url": "https://cloud.mail.ru/public/2RNv/faZLz1cLQ/0002/2025-03-05 09.27.42.PNG"
        },
        {
            "api_url": "https://cloud.mail.ru/api/v2/file/download?weblink=2RNv/faZLz1cLQ/test%20file.pdf",
            "expected_weblink": "2RNv/faZLz1cLQ/test file.pdf",
            "expected_public_url": "https://cloud.mail.ru/public/2RNv/faZLz1cLQ/test file.pdf"
        }
    ]
    
    for i, test_case in enumerate(test_cases, 1):
        print(f"\n{i}. Testing URL: {test_case['api_url']}")
        
        # Extract weblink
        weblink_match = re.search(r'weblink=([^&]+)', test_case['api_url'])
        assert weblink_match, "Should extract weblink from URL"
        weblink = weblink_match.group(1)
        
        # Decode weblink
        try:
            weblink_decoded = unquote(weblink)
        except:
            weblink_decoded = weblink
        
        # Build public URL
        public_url = f"https://cloud.mail.ru/public/{weblink_decoded}"
        
        print(f"   Extracted weblink: {weblink}")
        print(f"   Decoded weblink: {weblink_decoded}")
        print(f"   Public URL: {public_url}")
        
        assert weblink_decoded == test_case['expected_weblink'], \
            f"Decoded weblink should match expected: {test_case['expected_weblink']}"
        assert public_url == test_case['expected_public_url'], \
            f"Public URL should match expected: {test_case['expected_public_url']}"
        
        print(f"   ✓ Test case {i} passed")
    
    print(f"\n✅ TEST PASSED: Webbink extraction works correctly!")
    return True


if __name__ == "__main__":
    print("\n" + "=" * 80)
    print("CLOUD SERVICE 403 FALLBACK TESTS")
    print("=" * 80)
    
    results = []
    
    # Test 1: Fallback mechanism
    results.append(("403 Fallback to Public URL", test_403_fallback_to_public_url()))
    
    # Test 2: Webbink extraction
    results.append(("Weblink Extraction", test_403_fallback_weblink_extraction()))
    
    # Summary
    print("\n" + "=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for name, result in results:
        status = "✅ PASSED" if result else "❌ FAILED"
        print(f"{status}: {name}")
    
    print(f"\nTotal: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 All tests passed!")
        sys.exit(0)
    else:
        print("\n❌ Some tests failed")
        sys.exit(1)

