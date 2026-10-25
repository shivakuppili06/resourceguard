terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Recommended: use an S3 backend for real deployments.
  # backend "s3" {
  #   bucket = "resourceguard-tfstate"
  #   key    = "prod/terraform.tfstate"
  #   region = "ap-south-1"
  # }
}

provider "aws" {
  region = var.aws_region
}

module "vpc" {
  source = "./modules/vpc"

  name       = "${var.project_name}-vpc"
  cidr_block = var.vpc_cidr
  azs        = var.availability_zones
}

module "eks" {
  source = "./modules/eks"

  cluster_name    = "${var.project_name}-cluster"
  vpc_id          = module.vpc.vpc_id
  subnet_ids      = module.vpc.private_subnet_ids
  cluster_version = var.eks_version
  node_instance_type = var.node_instance_type
  node_desired_size  = var.node_desired_size
  node_min_size      = var.node_min_size
  node_max_size      = var.node_max_size
}
