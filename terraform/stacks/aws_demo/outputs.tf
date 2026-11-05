output "instance_id" { value = aws_instance.iipp_demo.id }
output "private_ip" { value = aws_instance.iipp_demo.private_ip }
output "instance_type" { value = aws_instance.iipp_demo.instance_type }
output "region" { value = var.region }
