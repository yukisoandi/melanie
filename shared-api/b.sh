set -gx ARROW_HOME /Users/m/Code/arrow/dist
set -gx CMAKE_PREFIX_PATH $ARROW_HOME $CMAKE_PREFIX_PATH
set -gx PYARROW_WITH_PARQUET 1
set -gx LD_LIBRARY_PATH /Users/m/Code/arrow/dist/lib $LD_LIBRARY_PATH

set -gx PYARROW_WITH_DATASET 1
set -gx CFLAGS -w -fPIC -O2 -march=native -pipe
set -gx CXXFLAGS $CFLAGS
set -gx PYARROW_PARALLEL 15
set -gx CC "sccache gcc"
set -gx CXX "sccache g++"

cmake  -DCMAKE_C_COMPILER_LAUNCHER=sccache -DCMAKE_CXX_COMPILER_LAUNCHER=sccache -DCMAKE_INSTALL_PREFIX=$ARROW_HOME -DARROW_COMPUTE=ON -DCMAKE_INSTALL_LIBDIR=lib -DARROW_CSV=ON -DARROW_VERBOSE_THIRDPARTY_BUILD=ON -GNinja -DPARQUET_REQUIRE_ENCRYPTION=ON -DPARQUET_BUILD_EXECUTABLES=ON -DARROW_S3=ON -DARROW_FLIGHT_SQL=ON -DARROW_FLIGHT=ON -DARROW_PLASMA=ON -DARROW_SIMD_LEVEL=AVX512 -DARROW_BUILD_TESTS=OFF -DARROW_ENABLE_TIMING_TESTS=OFF -DARROW_BUILD_UTILITIES=ON -DARROW_JEMALLOC=OFF -DARROW_MIMALLOC=ON -DARROW_TESTING=OFF -DARROW_DATASET=ON -DARROW_FILESYSTEM=ON -DARROW_HDFS=ON -DARROW_JSON=ON -DARROW_PARQUET=ON -DARROW_WITH_BROTLI=ON -DARROW_WITH_BZ2=ON -DARROW_WITH_LZ4=ON -DARROW_WITH_SNAPPY=ON -DARROW_WITH_ZLIB=ON -DARROW_PYTHON=ON -DARROW_WITH_ZSTD=ON -DProtobuf_SOURCE=BUNDLED -DCMAKE_BUILD_TYPE=Release -DPython3_EXECUTABLE=/Users/m/Opt/mamba/envs/melanie/bin/python -DDOWNLOAD_EXTRACT_TIMESTAMP=ON  -DARROW_UTF8PROC_USE_SHARED=ON -DUTF8PROC_SOURCE=BUNDLED -DARROW_DEPENDENCY_SOURCE=CONDA  ..

python setup.py build_ext  --cmake-generator Ninja --with-plasma     --with-s3   --with-parquet  --with-parquet-encryption    --with-flight   --bundle-arrow-cpp       --bundle-plasma-executable  bdist_wheel

python setup.py build_ext  --cmake-generator Ninja --with-plasma     --with-s3   --with-parquet  --with-parquet-encryption    --with-flight   --bundle-arrow-cpp       --bundle-plasma-executable  --extra-cmake-args="-Dutf8proc_LIB=/Users/m/Opt/mamba/envs/melanie/lib -Dutf8proc_INCLUDE_DIR=/Users/m/Opt/mamba/envs/melanie/include -DMACOSX_DEPLOYMENT_TARGET=13.3"   --bundle-cython-cpp    --cython-cplus
