FROM ubuntu:20.04

ARG http_proxy
ARG https_proxy
ENV http_proxy=${http_proxy}
ENV https_proxy=${https_proxy}

USER root
SHELL ["/bin/bash", "-c"]

##### Install git #####
RUN apt-get update --fix-missing && \
    apt-get install -y --no-install-recommends \
    ca-certificates \
    gnupg2 \
    git-core \
    curl \
    openssh-client && \
    update-ca-certificates && \
    git config --global http.sslVerify true

# Install Miniconda
RUN curl -fsSL https://mirrors.tuna.tsinghua.edu.cn/anaconda/miniconda/Miniconda3-latest-Linux-x86_64.sh -o /tmp/miniconda.sh && \
    bash /tmp/miniconda.sh -b -p /opt/conda && \
    rm /tmp/miniconda.sh && \
    /opt/conda/bin/conda clean -afy
ENV PATH="/opt/conda/bin:$PATH"
ENV PATH="/root/.local/bin:$PATH"

# Accept TOS
RUN conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/main && \
    conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/r && \
    conda config --remove channels defaults && \
    conda config --add channels https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/main/ && \
    conda config --add channels https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/free/

# Create environment
RUN conda create -n test python=3.10 -y

# Activate environment and install packages (e.g., numpy, scipy)
RUN /bin/bash -c "source activate test"

RUN mkdir -p /sel4-project
WORKDIR /sel4-project

##### Step 1: Clone the verification-manifest repository #####

RUN apt-get update --fix-missing && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y \
    gcc-arm-none-eabi gcc-aarch64-linux-gnu gcc-10-riscv64-linux-gnu \
    build-essential libxml2-utils ccache \
    ncurses-dev librsvg2-bin device-tree-compiler cmake \
    ninja-build curl zlib1g-dev texlive-fonts-recommended \
    texlive-latex-extra texlive-metapost texlive-bibtex-extra \
    mlton-compiler haskell-stack \
    rsync

WORKDIR /sel4-project/verification
COPY ./verification ./

WORKDIR /sel4-project/verification/l4v

RUN ln -s /sel4-project/verification/isabelle ./isabelle && \
    mkdir -p ~/.isabelle/etc && \
    cp -i misc/etc/settings ~/.isabelle/etc/settings

COPY ./isabelle_cache/* /root/.isabelle/contrib/

RUN ./isabelle/bin/isabelle components -a && \
    ./isabelle/bin/isabelle jedit -bf && \
    ./isabelle/bin/isabelle build -bv HOL

RUN pip install --user --upgrade pip && \
    pip install --user sel4-deps

WORKDIR /sel4-project/verification/l4v

CMD ["bash"]
