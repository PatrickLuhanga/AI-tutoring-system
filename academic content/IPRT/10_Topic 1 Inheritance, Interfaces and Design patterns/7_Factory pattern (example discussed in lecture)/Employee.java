package empfactory;

public abstract class Employee {
	protected char type;
	protected String staffId;
	protected String name;
	
	
	//Define constructors
	public Employee() {
		this.type=' ';
		this.staffId = "11111111";
		this.name = "No name";
	}
	public Employee(char type, String staffId, String name) {
		this.type=type;
		this.staffId = staffId;
		this.name = name;
	}
	public abstract double calcMonthlyPay();
	@Override
	public String toString() {
		return "Employee [type=" + type + ", staffId=" + staffId + ", name=" + name + ", calcMonthlyPay()="
				+ calcMonthlyPay() + "]";
	}
	
	
	
	
	
	

}
