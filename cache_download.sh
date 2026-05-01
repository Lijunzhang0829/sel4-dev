#!/bin/bash

# download if not exists
download_if_not_exists() {
    local url=$1
    local filename=$(basename "$url")
    if [ -f "$filename" ]; then
        echo "file $filename already exists, skip download"
    else
        echo "download $filename..."
        wget "$url"
    fi
}

# download Isabelle components
mkdir -p ./isabelle_cache
cd ./isabelle_cache
# download all components
download_if_not_exists "https://isabelle.sketis.net/components/gnu-utils-20211030.tar.gz"
download_if_not_exists "https://isabelle.sketis.net/components/bash_process-20240326.tar.gz"
download_if_not_exists "https://isabelle.sketis.net/components/csdp-6.1.1.tar.gz"
download_if_not_exists "https://isabelle.sketis.net/components/cvc4-1.8.tar.gz"
download_if_not_exists "https://isabelle.sketis.net/components/e-3.0.03-1.tar.gz"
download_if_not_exists "https://isabelle.sketis.net/components/easychair-3.5.tar.gz"
download_if_not_exists "https://isabelle.sketis.net/components/eptcs-1.7.0.tar.gz"
download_if_not_exists "https://isabelle.sketis.net/components/flatlaf-2.6.tar.gz"
download_if_not_exists "https://isabelle.sketis.net/components/foiltex-2.1.4b.tar.gz"
download_if_not_exists "https://isabelle.sketis.net/components/idea-icons-20210508.tar.gz"
download_if_not_exists "https://isabelle.sketis.net/components/isabelle_fonts-20211004.tar.gz"
download_if_not_exists "https://isabelle.sketis.net/components/isabelle_setup-20240327.tar.gz"
download_if_not_exists "https://isabelle.sketis.net/components/javamail-20240109.tar.gz"
download_if_not_exists "https://isabelle.sketis.net/components/jedit-20240425.tar.gz"
download_if_not_exists "https://isabelle.sketis.net/components/jfreechart-1.5.3.tar.gz"
download_if_not_exists "https://isabelle.sketis.net/components/jortho-1.0-2.tar.gz"
download_if_not_exists "https://isabelle.sketis.net/components/jsoup-1.17.2.tar.gz"
download_if_not_exists "https://isabelle.sketis.net/components/kodkodi-1.5.7.tar.gz"
download_if_not_exists "https://isabelle.sketis.net/components/lipics-3.1.3.tar.gz"
download_if_not_exists "https://isabelle.sketis.net/components/llncs-2.23.tar.gz"
download_if_not_exists "https://isabelle.sketis.net/components/minisat-2.2.1-1.tar.gz"
download_if_not_exists "https://isabelle.sketis.net/components/mlton-20210117-3.tar.gz"
download_if_not_exists "https://isabelle.sketis.net/components/pdfjs-2.14.305.tar.gz"
download_if_not_exists "https://isabelle.sketis.net/components/bib2xhtml-20190409.tar.gz"
download_if_not_exists "https://isabelle.sketis.net/components/nunchaku-0.5.tar.gz"
download_if_not_exists "https://isabelle.sketis.net/components/opam-2.0.7.tar.gz"
download_if_not_exists "https://isabelle.sketis.net/components/polyml-5.9.1.tar.gz"
download_if_not_exists "https://isabelle.sketis.net/components/postgresql-42.7.3.tar.gz"
download_if_not_exists "https://isabelle.sketis.net/components/prismjs-1.29.0.tar.gz"
download_if_not_exists "https://isabelle.sketis.net/components/rsync-3.2.7-1.tar.gz"
download_if_not_exists "https://isabelle.sketis.net/components/scala-3.3.3.tar.gz"
download_if_not_exists "https://isabelle.sketis.net/components/smbc-0.4.1.tar.gz"
download_if_not_exists "https://isabelle.sketis.net/components/spass-3.8ds-2.tar.gz"
download_if_not_exists "https://isabelle.sketis.net/components/sqlite-3.45.2.0.tar.gz"
download_if_not_exists "https://isabelle.sketis.net/components/stack-2.15.5.tar.gz"
download_if_not_exists "https://isabelle.sketis.net/components/vampire-4.8.tar.gz"
download_if_not_exists "https://isabelle.sketis.net/components/verit-2021.06.2-rmx-1.tar.gz"
download_if_not_exists "https://isabelle.sketis.net/components/vscode_extension-20230206.tar.gz"
download_if_not_exists "https://isabelle.sketis.net/components/vscodium-1.70.1.tar.gz"
download_if_not_exists "https://isabelle.sketis.net/components/xz-java-1.9.tar.gz"
download_if_not_exists "https://isabelle.sketis.net/components/z3-4.4.0pre-4.tar.gz"
download_if_not_exists "https://isabelle.sketis.net/components/zipperposition-2.1-1.tar.gz"
download_if_not_exists "https://isabelle.sketis.net/components/zstd-jni-1.5.5-4.tar.gz"
download_if_not_exists "https://isabelle.sketis.net/components/naproche-20240519.tar.gz"
echo "Isabelle components downloaded"
cd ..

# download Stack components
mkdir -p ./stack_cache
cd ./stack_cache
download_if_not_exists "https://github.com/commercialhaskell/stack/releases/download/v3.7.1/stack-3.7.1-linux-x86_64.tar.gz"
echo "Stack components downloaded"
cd ..





