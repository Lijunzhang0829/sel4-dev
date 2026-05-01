# seL4-docker

#### Download l4v
```shell
curl -o /tmp/repo https://storage.googleapis.com/git-repo-downloads/repo && \
    chmod a+x /tmp/repo 

mkdir verification && cd verification && \
repo init -u https://git@github.com/seL4/verification-manifest.git && \
repo sync
```

#### Tweak version
- l4v (v13.0.0)
```shell
pushd . && \
cd verification/l4v &&
git checkout seL4-13.0.0 && \
popd
```
- seL4 (v13.0.0)
```shell
pushd . && \
cd verification/seL4 && \
git checkout 13.0.0 && \
popd
```
- Isabelle (v2024)
```shell
pushd . && \
cd verification/isabelle && \
git checkout Isabelle2024 && \
popd
```

#### Download dataset
```
pushd . && \
cd seL4-prover/datasets && \
wget https://github.com/FVELER/FVELerExtraction/raw/main/FVELer.zip && \
unzip FVELer.zip && \
popd
```

#### Build docker
```shell
sudo docker build --network=host --progress=plain -t sel4-test .
```

#### Run docker
Run the following commands
```shell
sudo docker run -it --network=host sel4-test
```
or docker pull
```shell
sudo docker pull lizenan1995/sel4-test:latest
```

