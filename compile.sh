#!/bin/bash

MAX_RETRIES=3  # max retries
retry_count=0
success=false

while [ $retry_count -lt $MAX_RETRIES ] && [ "$success" = false ]; do
    # run tests and save output to temporary file
    L4V_ARCH=ARM ./run_tests -j 128 --no-timeout | tee test_output.log
    
    # check if there are failed tests
    if grep -q "FAILED \*" test_output.log || grep -q "TIMEOUT \*" test_output.log; then
        retry_count=$((retry_count + 1))
        echo "detected failed or timeout, this is the $retry_countth attempt"
        if [ $retry_count -lt $MAX_RETRIES ]; then
            echo "waiting 10 seconds before retrying..."
            sleep 10
        fi
    else
        success=true
        echo "all tests passed!"
    fi
done

# clean up temporary file
rm -f test_output.log

if [ "$success" = false ]; then
    echo "failed after $MAX_RETRIES attempts, please check the log for details"
    exit 1
else
    echo "all tests passed!"
    exit 0
fi