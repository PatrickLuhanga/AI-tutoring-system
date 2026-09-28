package sum;

public class Sum {
	//data members
	private double num1;
	private double num2;
	
	//constructor
	public Sum() {
		num1=0;
		num2=0;
	}
	public Sum(double num1, double num2) {
		super();
		this.num1 = num1;
		this.num2 = num2;
	}
	public double getNum1() {
		return num1;
	}
	public void setNum1(double num1) {
		this.num1 = num1;
	}
	public double getNum2() {
		return num2;
	}
	public void setNum2(double num2) {
		this.num2 = num2;
	}
	public double add() {
		return num1+num2;	
	}
	public String toString() {
		return "The sum of " + num1 + " " + num2 + " is: " +add();
	}

}
